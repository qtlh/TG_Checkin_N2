import asyncio, logging, os, sys
from datetime import datetime, timezone, timedelta
from telethon import TelegramClient
from telethon.sessions import StringSession

# ── 从环境变量读取 ──────────────────────────────────
SESSION  = os.environ["TG_SESSION"]
API_ID   = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]

# ── 签到目标 ────────────────────────────────────────
TARGETS = {
    "@yzaws_bot":             "/checkin",      #大姨子
    "@Lewa_user_bot":         "/start",        #乐蛙
    "@Lamhosting_bot":        "/start",        #台湾服务器
    "@llIlIlIllbot":          "/start",        #接码
}

# ── 日志（北京时间）────────────────────────────────
BEIJING_TZ = timezone(timedelta(hours=8))
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.Formatter.converter = lambda *args: datetime.now(BEIJING_TZ).timetuple()


async def checkin_task(client, bot, command):
    try:
        logging.info(f"正在处理 {bot}...")
        await client.send_message(bot, command)
        await asyncio.sleep(15)

        history = await client.get_messages(bot, limit=1)
        if not history:
            logging.warning(f"{bot} 暂无回复。")
            return

        msg = history[0]
        preview = msg.text.split("\n")[0] if msg.text else "（无文字内容）"
        logging.info(f"{bot} 回复: {preview}")

        if msg.buttons:
            for row in msg.buttons:
                for btn in row:
                    if any(w.lower() in btn.text.lower()
                           for w in ["签到", "check", "确认", "领取", "点我", "sign"]):
                        logging.info(f"点击按钮 [{btn.text}]...")
                        await msg.click(text=btn.text)
                        await asyncio.sleep(5)
                        return
            logging.info(f"{bot} 有按钮但未匹配关键词，跳过。")
        else:
            logging.info(f"{bot} 无按钮，已通过文本确认。")

    except Exception as e:
        logging.error(f"{bot} 异常: {e}")


async def main():
    logging.info("=" * 40 + " 签到启动 " + "=" * 40)
    client = TelegramClient(
        StringSession(SESSION), API_ID, API_HASH,
        device_model="iPhone 15 Pro",
        system_version="17.4.1",
        app_version="10.11.0",
        receive_updates=False,
    )
    async with client:
        for bot, cmd in TARGETS.items():
            await checkin_task(client, bot, cmd)
            await asyncio.sleep(10)

    logging.info("任务结束")
    logging.info("=" * 40 + " 签到完成 " + "=" * 40)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logging.critical(f"崩溃: {e}")
        sys.exit(1)