import discord
from discord.ext import tasks
from google import genai
import asyncio
import os
from datetime import datetime, timedelta

# ================= Railway 환경 변수 자동 연동 =================
# .strip()을 붙여 환경변수 앞뒤에 혹시 모를 공백이나 줄바꿈을 제거합니다.
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

TARGET_CHANNEL_ID = None  
SILENCE_TIMEOUT = 1800 
# ============================================================

# 기본 인텐트 설정 및 가동
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

# API 키가 정상적으로 로드되었을 때만 클라이언트 초기화
if GEMINI_API_KEY:
    ai_client = genai.Client(api_key=GEMINI_API_KEY)
else:
    ai_client = None
    print("경고: GEMINI_API_KEY 환경 변수를 찾을 수 없습니다.")

last_message_time = datetime.now()
last_channel = None

@bot.event
async def on_ready():
    print(f'{bot.user} 봇이 성공적으로 로그인했습니다!')
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

    should_reply = (
        bot.user.mentioned_in(message) or 
        bot.user.name in message.content or 
        (asyncio.get_event_loop().time() % 5 == 0)
    )

    if should_reply:
        async with message.channel.typing():
            try:
                prompt = (
                    f"너는 디스코드 서버에서 사람들과 어울리는 장난기 많고 친근한 10~20대 친구야. "
                    f"절대 존댓말(~해요, ~입니다, ~습니다)을 쓰지 말고 100% 편한 반말만 사용해줘. "
                    f"상대방이 '{message.content}'라고 말했어. 이 대화에 자연스럽게 끼어들거나 "
                    f"맞장구치는 짧은 답변을 친구처럼 한두 문장으로 해줘. 이모지(ㅋㅋ, ㅎㅎ, 😭 등)도 섞어줘."
                )
                response = ai_client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt
                )
                await message.channel.send(response.text)
            except Exception as e:
                print(f"끼어들기 에러: {e}")

@tasks.loop(seconds=5)
async def check_silence():
    global last_message_time, last_channel
    
    if datetime.now() - last_message_time > timedelta(seconds=SILENCE_TIMEOUT):
        target = bot.get_channel(TARGET_CHANNEL_ID) if TARGET_CHANNEL_ID else last_channel
        
        if target and ai_client:
            try:
                last_message_time = datetime.now()
                
                prompt = (
                    "너는 심심해진 디스코드 대화방에 먼저 말을 거는 친근하고 쾌활한 친구야. "
                    "절대 존댓말을 쓰지 말고 무조건 편한 반말로 작성해줘. "
                    "다들 조용해서 심심하니까 '얘들아 뭐해?', '다들 자냐?', '심심한데 나랑 놀 사람' 같이 "
                    "가볍게 툭 던지는 질문이나 대화 주제를 딱 한 문장으로만 보내줘. 챗봇 티 내지 마."
                )
                response = ai_client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt
                )
                await target.send(response.text)
            except Exception as e:
                print(f"선톡 에러: {e}")

if not DISCORD_TOKEN:
    print("에러: DISCORD_TOKEN 환경 변수가 비어 있습니다. Railway 설정을 확인하세요.")
else:
    bot.run(DISCORD_TOKEN)
