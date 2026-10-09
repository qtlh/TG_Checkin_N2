import os
import sys
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    ChatWriteForbiddenError,
    UserIsBlockedError,
    PeerIdInvalidError,
    FloodWaitError,
    RPCError,
)

# ================= 配置区域 =================

TARGETS = {
    "@yzaws_bot":           "/checkin",    #大姨子
    3219886860:             "/checkin",    # DNSHE域名
}

# 新增：群组数字 ID 签到列表
GROUP_CHECKINS = [
#    (-1003219886860, "/checkin"),        # DNSHE域名
]

# 真正的签到按钮关键词，只有匹配到这里的按钮，才会被当成真正签到按钮点击
CHECKIN_KEYWORDS = ["签到", "每日签到", "前往签到", "立即签到", "check", "确认", "领取", "点我", "sign", "✅每日签到", "✅"]

# 有些机器人不会返回按钮，只用纯文本提示签到状态；这些反馈也视为签到成功
SUCCESS_FEEDBACKS = ["签到", "签到成功", "签到/签到成功"]

API_ID = os.environ.get("TG_API_ID")
API_HASH = os.environ.get("TG_API_HASH")
SESSION = os.environ.get("TG_SESSION")

if not all([API_ID, API_HASH, SESSION]):
    print("错误: 请确保已设置 TG_API_ID, TG_API_HASH 和 TG_SESSION 环境变量。")
    sys.exit(1)

try:
    API_ID = int(API_ID)
except ValueError:
    print("错误: TG_API_ID 必须是数字。")
    sys.exit(1)

BEIJING_TZ = timezone(timedelta(hours=8))

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logging.Formatter.converter = lambda *args: datetime.now(BEIJING_TZ).timetuple()


# ================= 工具函数 =================

def short_text(text, max_len=220):
    """把多行文本压成一行，避免日志太长。"""
    if not text:
        return "（无内容）"
    text = " ".join(str(text).splitlines()).strip()
    return text[:max_len] + "..." if len(text) > max_len else text


def normalize_bot_name(bot):
    """
    修正类似 @@xxx 的写法；如果为整型数字 ID 则保持原样。
    """
    if isinstance(bot, int):
        return bot

    bot = str(bot).strip()
    while bot.startswith("@@"):
        bot = bot[1:]

    if not bot.startswith("@") and not bot.lstrip("-").isdigit():
        bot = "@" + bot

    # 如果纯字符串形式也是数字，转换成 int 类型以便 Telethon 处理
    if isinstance(bot, str) and bot.lstrip("-").isdigit():
        bot = int(bot)

    return bot


def button_matches(button_text, keywords):
    """判断按钮文本是否匹配指定关键词。"""
    button_text = button_text or ""
    return any(keyword.lower() in button_text.lower() for keyword in keywords)


def is_checkin_button(button_text):
    """判断是否是真正的签到按钮。"""
    return button_matches(button_text, CHECKIN_KEYWORDS)


def is_success_feedback(message_text):
    """判断纯文本反馈是否表示签到成功。"""
    message_text = short_text(message_text or "")
    compact_text = "".join(message_text.split())
    return any(feedback in compact_text for feedback in SUCCESS_FEEDBACKS)


def classify_failure_reason(reason):
    """给失败原因做简单分类。"""
    reason = reason or ""
    if "无法写入消息" in reason:
        return "无写入权限"
    if "频率限制" in reason:
        return "频率限制"
    if "目标无效" in reason:
        return "目标无效"
    if "未匹配到任何带内联按钮" in reason:
        return "没有按钮"
    if "没有找到签到按钮" in reason:
        return "没有签到按钮"
    if "无法与其对话" in reason or "拉黑" in reason:
        return "无法对话"
    if "RPC 错误" in reason:
        return "Telegram RPC 错误"
    if "点击签到按钮异常" in reason:
        return "签到按钮异常"
    if "异常" in reason:
        return "程序异常"
    return "其他错误"


def make_result(ok, bot, command, reason, last_message="（无）"):
    """统一返回结果结构。"""
    return {
        "ok": ok,
        "bot": str(bot),
        "command": command,
        "category": "成功" if ok else classify_failure_reason(reason),
        "reason": reason,
        "last_message": last_message,
    }


async def get_last_message_preview(client, bot):
    """获取目标会话最后一条消息，失败时返回占位文本。"""
    try:
        history = await client.get_messages(bot, limit=1)
        if not history:
            return "（无历史消息）"
        msg = history[0]
        if getattr(msg, "text", None):
            return short_text(msg.text)
        if getattr(msg, "message", None):
            return short_text(msg.message)
        if getattr(msg, "buttons", None):
            return "（最后消息包含按钮，但无文本内容）"
        return "（最后消息无文本内容）"
    except Exception as e:
        return f"（获取最后消息失败: {e}）"


def get_message_preview(msg):
    """从消息对象提取简短文本。"""
    if not msg:
        return "（无消息）"
    if getattr(msg, "text", None):
        return short_text(msg.text)
    if getattr(msg, "message", None):
        return short_text(msg.message)
    if getattr(msg, "buttons", None):
        return "（消息包含按钮，但无文本内容）"
    return "（消息无文本内容）"


def get_all_buttons_text(msg):
    """获取消息里的所有按钮文本。"""
    buttons = []
    if not msg or not getattr(msg, "buttons", None):
        return buttons
    for row in msg.buttons:
        for btn in row:
            buttons.append(btn.text or "")
    return buttons


async def get_latest_message_with_buttons(client, bot, wait_seconds=25, limit=5):
    """等待并获取最近带按钮的消息。"""
    msg_with_buttons = None
    last_message = "（尚未获取消息）"

    for i in range(wait_seconds):
        await asyncio.sleep(1)
        history = await client.get_messages(bot, limit=limit)
        if history:
            newest = history[0]
            last_message = get_message_preview(newest)
            for m in history:
                if m and getattr(m, "buttons", None):
                    msg_with_buttons = m
                    break
        if msg_with_buttons:
            logging.info(f"在第 {i + 1} 秒获取到带按钮的消息。")
            break

    return msg_with_buttons, last_message


async def print_me(client):
    """打印当前登录账号信息。"""
    try:
        me = await client.get_me()
        username = f"@{me.username}" if getattr(me, "username", None) else "无用户名"
        phone = getattr(me, "phone", None) or "无手机号信息"
        is_bot = getattr(me, "bot", False)

        logging.info(f"当前登录账号 ID: {me.id}")
        logging.info(f"当前登录账号用户名: {username}")
        logging.info(f"当前登录账号手机号: {phone}")
        logging.info(f"当前登录账号是否为 Bot: {is_bot}")

        if is_bot:
            logging.warning(
                "当前 TG_SESSION 是 Bot Session。Bot 账号通常不能给另一个 Bot 主动发消息，请使用普通用户账号生成 StringSession。"
            )
    except Exception as e:
        logging.warning(f"获取当前账号信息失败: {e}")


# ================= 签到任务 =================

async def checkin_task(client, bot, command):
    """执行签到任务。"""
    bot = normalize_bot_name(bot)
    last_message = "（尚未获取消息）"

    try:
        logging.info("-" * 40)
        logging.info(f"正在处理: {bot}")
        logging.info(f"发送命令: {command}")

        try:
            await client.send_message(bot, command)
        except ChatWriteForbiddenError:
            reason = "无法写入消息。可能无发言权限、被禁言或目标不可发言。"
            last_message = "（发送命令失败，未获取最后消息）"
            logging.error(f"{bot} {reason} 已跳过。")
            return make_result(False, bot, command, reason, last_message)
        except UserIsBlockedError:
            reason = "当前账号无法与其对话，可能是你拉黑了它或被限制。"
            last_message = "（当前账号无法与目标对话）"
            logging.error(f"{bot} {reason} 已跳过。")
            return make_result(False, bot, command, reason, last_message)
        except PeerIdInvalidError:
            reason = "目标无效。请确保已加入群组或启动了 Bot。"
            last_message = "（目标无效，无法获取最后消息）"
            logging.error(f"{bot} {reason} 已跳过。")
            return make_result(False, bot, command, reason, last_message)
        except FloodWaitError as e:
            reason = f"触发 Telegram 频率限制，需要等待 {e.seconds} 秒。"
            last_message = await get_last_message_preview(client, bot)
            logging.warning(reason)
            await asyncio.sleep(e.seconds + 3)
            return make_result(False, bot, command, reason, last_message)
        except RPCError as e:
            reason = f"Telegram RPC 错误: {e}"
            last_message = await get_last_message_preview(client, bot)
            logging.error(f"{bot} {reason}")
            return make_result(False, bot, command, reason, last_message)

        msg_with_buttons, last_message = await get_latest_message_with_buttons(
            client,
            bot,
            wait_seconds=25,
            limit=5,
        )

        if not msg_with_buttons:
            last_message = await get_last_message_preview(client, bot)
            if is_success_feedback(last_message):
                reason = "收到签到成功反馈"
                logging.info(f"{reason}: {last_message}")
                return make_result(True, bot, command, reason, last_message)

            reason = "未匹配到任何带内联按钮的消息。"
            logging.warning(f"{reason} 最后消息: {last_message}")
            return make_result(False, bot, command, reason, last_message)

        panel_message = get_message_preview(msg_with_buttons)
        last_message = panel_message
        all_buttons_text = get_all_buttons_text(msg_with_buttons)

        logging.info(f"当前面板消息: {panel_message}")
        logging.info(f"当前按钮列表: {all_buttons_text}")

        checkin_button = None
        for row in msg_with_buttons.buttons:
            for btn in row:
                if is_checkin_button(btn.text or ""):
                    checkin_button = btn
                    break
            if checkin_button:
                break

        if not checkin_button:
            reason = f"当前面板没有找到签到按钮。当前所有按钮为: {all_buttons_text}"
            logging.warning(reason)
            return make_result(False, bot, command, reason, last_message)

        btn_text = checkin_button.text or ""
        logging.info(f"找到签到按钮，正在点击 [{btn_text}]...")

        try:
            click_result = await checkin_button.click()
            await asyncio.sleep(6)

            popup_message = getattr(click_result, "message", None)
            if popup_message:
                last_message = short_text(popup_message)
                logging.info(f"按钮弹窗反馈: {last_message}")
            else:
                last_message = await get_last_message_preview(client, bot)

            logging.info(f"最终签到反馈: {last_message}")
            reason = "成功点击签到按钮"
            return make_result(True, bot, command, reason, last_message)

        except FloodWaitError as e:
            reason = f"点击签到按钮时触发 Telegram 频率限制，需要等待 {e.seconds} 秒。"
            last_message = await get_last_message_preview(client, bot)
            logging.warning(reason)
            await asyncio.sleep(e.seconds + 3)
            return make_result(False, bot, command, reason, last_message)
        except RPCError as e:
            reason = f"点击签到按钮时 Telegram RPC 错误: {e}"
            last_message = await get_last_message_preview(client, bot)
            logging.error(reason)
            return make_result(False, bot, command, reason, last_message)
        except Exception as e:
            reason = f"点击签到按钮异常: {e}"
            last_message = await get_last_message_preview(client, bot)
            logging.error(reason, exc_info=True)
            return make_result(False, bot, command, reason, last_message)

    except Exception as e:
        reason = f"异常: {str(e)}"
        last_message = await get_last_message_preview(client, bot)
        logging.error(f"{bot} {reason}", exc_info=True)
        return make_result(False, bot, command, reason, last_message)


# ================= 主函数 =================

async def main():
    # 合并 TARGETS 字典与 GROUP_CHECKINS 列表
    all_targets = list(TARGETS.items()) + GROUP_CHECKINS

    logging.info("=" * 40 + " 签到启动 " + "=" * 40)
    logging.info(f"共 {len(all_targets)} 个目标")

    client = TelegramClient(
        StringSession(SESSION),
        API_ID,
        API_HASH,
        device_model="iPhone 15 Pro",
        system_version="17.4.1",
        app_version="10.11.0",
        receive_updates=False,
    )

    success_items = []
    failed_items = []

    async with client:
        await print_me(client)

        for bot, cmd in all_targets:
            result = await checkin_task(client, bot, cmd)

            if result["ok"]:
                success_items.append(result)
            else:
                failed_items.append(result)

            await asyncio.sleep(8)

    logging.info("=" * 40 + " 签到完成 " + "=" * 40)
    logging.info(f"成功: {len(success_items)}，失败: {len(failed_items)}")

    if failed_items:
        logging.info("=" * 40 + " 失败列表 " + "=" * 40)
        for index, item in enumerate(failed_items, start=1):
            logging.info(
                f"{index}. 目标: {item['bot']} | 命令: {item['command']} | "
                f"类型: {item['category']} | 原因: {item['reason']} | 最后消息: {item['last_message']}"
            )
    else:
        logging.info("没有失败目标。")

    if success_items:
        logging.info("=" * 40 + " 成功列表 " + "=" * 40)
        for index, item in enumerate(success_items, start=1):
            logging.info(
                f"{index}. 目标: {item['bot']} | 命令: {item['command']} | "
                f"原因: {item['reason']} | 最后消息: {item['last_message']}"
            )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("手动中断。")
        sys.exit(0)
    except Exception as e:
        logging.critical(f"崩溃: {e}", exc_info=True)
        sys.exit(1)
