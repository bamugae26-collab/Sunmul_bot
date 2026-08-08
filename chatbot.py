import discord
from discord.ext import tasks
from google import genai
import asyncio
import os
import random
import re  # 텍스트 정제를 위한 정규식 라이브러리
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

    is_called = bot.user.mentioned_in(message) or (bot.user.name in message.content)
    is_random_interrup = random.random() < 0.3

    if is_called or is_random_interrup:
        # 봇이 입력 중인 표시 켜기
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
                
                # 가공되지 않은 텍스트 추출
                reply_text = str(response.text).strip()
                
                # [안전장치] 만약 제미나이가 빈 대답을 보냈거나 오류 문자가 섞였다면 기본 멘트 출력
                if not reply_text:
                    reply_text = "어.. 뭐라고? 잘 못 들었어 ㅋㅋ"
                
                # 디스코드 전송 제한을 위해 글자수 컷
                if len(reply_text) > 1900:
                    reply_text = reply_text[:1900] + "..."
                
                # 최종 디스코드 채널로 답장 전송
                await message.channel.send(reply_text)
                
            except discord.errors.Forbidden:
                print("권한 에러: 봇이 이 채널에 메시지나 링크를 보낼 권한이 없습니다. 서버 설정을 확인하세요.")
            except Exception as e:
                print(f"시스템 오류 발생: {e}")
                # 최소한의 대답이라도 강제 전송 시도
                try:
                    await message.channel.send("어 왜 불렀어? 무슨 일 있어? ㅋㅋ")
                except:
                    pass

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
                await target.send(str(response.text).strip())
            except Exception as e:
                print(f"선톡 에러: {e}")

if not DISCORD_TOKEN:
    print("에러: DISCORD_TOKEN 환경 변수가 비어 있습니다.")
else:
    bot.run(DISCORD_TOKEN)
