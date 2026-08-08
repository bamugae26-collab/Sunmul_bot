import discord
from discord.ext import tasks
from google import genai
import asyncio
import os
from datetime import datetime, timedelta

# ================= Railway 환경 변수 자동 연동 =================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

TARGET_CHANNEL_ID = None  
# 선톡 기준을 30분(1800초)으로 넉넉하게 설정
SILENCE_TIMEOUT = 1800 
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

if GEMINI_API_KEY:
    ai_client = genai.Client(api_key=GEMINI_API_KEY)
else:
    ai_client = None

last_message_time = datetime.now()
last_channel = None

@bot.event
async def on_ready():
    print(f'{bot.user} 봇 로그인 성공! (API 절약 모드)')
    global last_message_time
    last_message_time = datetime.now()
    if not check_silence.is_running():
        check_silence.start()

@bot.event
async def on_message(message):
    global last_message_time, last_channel
    if message.author == bot.user or not ai_client:
        return

    last_message_time = datetime.now()
    last_channel = message.channel

    # [API 절약 변경점] 봇 이름이 불리거나 @태그 당했을 때만 100% 칼답 (무작위 끼어들기 일시 중지)
    is_called = bot.user.mentioned_in(message) or (bot.user.name in message.content)

    if is_called:
        async with message.channel.typing():
            try:
                prompt = (
                    "너는 디스코드 서버에서 사람들과 어울리는 장난기 많고 친근한 10~20대 친구야.\n"
                    "절대 존댓말을 쓰지 말고 100% 편한 반말만 사용해줘. 이모지도 섞어줘.\n\n"
                    f"상대방 내용: '{message.content}'\n"
                    "이 대화에 맞장구치는 답변을 친구처럼 한두 문장으로 해줘."
                )
                response = ai_client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt
                )
                await message.channel.send(str(response.text).strip())
            except Exception as e:
                await message.channel.send(f"😭 제미나이 한도 초과 오류 발생: {str(e)[:50]}")

# [API 절약 변경점] 감시 주기를 5초에서 5분(300초)으로 대폭 늘려 구글 차단 방지
@tasks.loop(seconds=300) 
async def check_silence():
    global last_message_time, last_channel
    if datetime.now() - last_message_time > timedelta(seconds=SILENCE_TIMEOUT):
        target = bot.get_channel(TARGET_CHANNEL_ID) if TARGET_CHANNEL_ID else last_channel
        if target and ai_client:
            try:
                last_message_time = datetime.now()
                prompt = (
                    "너는 심심해진 디스코드 대화방에 먼저 말을 거는 친근하고 쾌활한 친구야.\n"
                    "무조건 편한 반말로 '얘들아 뭐해?', '다들 자냐?' 같은 대화 주제를 딱 한 문장으로만 보내줘."
                )
                response = ai_client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt
                )
                await target.send(str(response.text).strip())
            except:
                pass

bot.run(DISCORD_TOKEN)
