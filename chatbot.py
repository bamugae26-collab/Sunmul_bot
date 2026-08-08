import discord
from discord.ext import tasks
from google import genai
import asyncio
import os
import random
from datetime import datetime, timedelta

# ================= Railway 환경 변수 자동 연동 =================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

TARGET_CHANNEL_ID = None  
SILENCE_TIMEOUT = 1800 
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

# 최신 구글 genai SDK 초기화 방식
if GEMINI_API_KEY:
    ai_client = genai.Client(api_key=GEMINI_API_KEY)
else:
    ai_client = None
    print("경고: GEMINI_API_KEY 환경 변수가 설정되지 않았습니다.")

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
    
    if message.author == bot.user:
        return

    last_message_time = datetime.now()
    last_channel = message.channel

    is_called = bot.user.mentioned_in(message) or (bot.user.name in message.content)
    is_random_interrup = random.random() < 0.3

    if is_called or is_random_interrup:
        if not ai_client:
            await message.channel.send("⚠️ 구글 제미나이 API 키 변수가 설정되지 않았어. Railway 대시보드를 확인해줘!")
            return

        async with message.channel.typing():
            try:
                # 꼬임 방지를 위해 시스템 명령어와 사용자 요청을 하나로 합친 단일 문자열 프롬프트 사용
                prompt = (
                    "너는 디스코드 서버에서 사람들과 어울리는 장난기 많고 친근한 10~20대 친구야.\n"
                    "절대 존댓말(~해요, ~입니다, ~습니다)을 쓰지 말고 100% 편한 반말만 사용해줘.\n"
                    "이모지(ㅋㅋ, ㅎㅎ, 😭 등)도 적절히 섞어줘.\n\n"
                    f"상대방의 대화내용: '{message.content}'\n"
                    "이 대화에 자연스럽게 끼어들거나 맞장구치는 답변을 친구처럼 한두 문장으로 해줘."
                )

                # 最新 google-genai 라이브러리 표준 가동 방식
                response = ai_client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt
                )
                
                reply_text = str(response.text).strip()
                
                if reply_text:
                    if len(reply_text) > 1900:
                        reply_text = reply_text[:1900] + "..."
                    await message.channel.send(reply_text)
                else:
                    await message.channel.send("어.. 제미나이가 빈 응답을 보냈네? 다시 말해봐 ㅋㅋ")
                
            except Exception as e:
                # 💡 핵심 디버깅 구문: 어떤 에러 때문에 튕겼는지 디스코드 창에 그대로 출력시킵니다.
                error_msg = f"😭 제미나이 API 호출 중 진짜 에러가 났어!\n`에러 내용: {str(e)}`"
                print(error_msg)
                await message.channel.send(error_msg)

@tasks.loop(seconds=5)
async def check_silence():
    global last_message_time, last_channel
    
    if datetime.now() - last_message_time > timedelta(seconds=SILENCE_TIMEOUT):
        target = bot.get_channel(TARGET_CHANNEL_ID) if TARGET_CHANNEL_ID else last_channel
        
        if target and ai_client:
            try:
                last_message_time = datetime.now()
                
                prompt = (
                    "너는 심심해진 디스코드 대화방에 먼저 말을 거는 친근하고 쾌활한 친구야.\n"
                    "절대 존댓말을 쓰지 말고 무조건 편한 반말로 작성해줘.\n"
                    "다들 조용해서 심심하니까 '얘들아 뭐해?', '다들 자냐?' 같은 대화 주제를 딱 한 문장으로만 보내줘."
                )
                response = ai_client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt
                )
                await target.send(str(response.text).strip())
            except Exception as e:
                print(f"선톡 에러: {e}")

if not DISCORD_TOKEN:
    print("에러: DISCORD_TOKEN 환경 변수가 비어 있습니다.")
else:
    bot.run(DISCORD_TOKEN)
