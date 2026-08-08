import discord
from discord.ext import tasks
from google import genai
import asyncio
import os
import aiohttp  # 백업 Groq API 비동기 통신용
import json
from datetime import datetime, timedelta

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()  # Groq 대시보드에서 발급받은 키

TARGET_CHANNEL_ID = None  
SILENCE_TIMEOUT = 1800 

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

# 메인 제미나이 클라이언트 초기화
if GEMINI_API_KEY:
    ai_client = genai.Client(api_key=GEMINI_API_KEY)
else:
    ai_client = None

last_message_time = datetime.now()
last_channel = None

# Groq 비동기 호출을 위한 백업 함수
async def generate_with_groq(prompt):
    """제미나이 API가 실패하거나 크레딧이 바닥나면 Groq(Llama 모델)으로 전환합니다."""
    if not GROQ_API_KEY:
        return "😭 제미나이 한도가 초과되었는데 백업 API 키(GROQ_API_KEY)도 등록되어 있지 않아."
        
    url = "https://groq.com"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",  # 매우 빠르고 고성능인 무료 지원 모델
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7
    }
    try:
        # 태블릿 네트워크 불안정을 대비해 10초 타임아웃 지정
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    result = await response.json()
                    return result["choices"][0]["message"]["content"].strip()
                elif response.status == 429:
                    return "😭 백업 엔진인 Groq 마저도 일시적인 한도 초과(429) 상태야. 잠시만 기다려줘!"
                else:
                    return f"❌ 백업 엔진 에러 발생 (코드: {response.status})"
    except Exception as e:
        return f"❌ 백업 엔진 연결 실패: {str(e)[:50]}"

@bot.event
async def on_ready():
    print(f'{bot.user} 봇 로그인 성공! (Gemini -> Groq 하이브리드 모드)')
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

    if is_called:
        async with message.channel.typing():
            prompt = (
                "너는 디스코드 서버에서 사람들과 어울리는 장난기 많고 친근한 10~20대 친구야.\n"
                "절대 존댓말을 쓰지 말고 100% 편한 반말만 사용해줘. 이모지도 섞어줘.\n\n"
                f"상대방 내용: '{message.content}'\n"
                "이 대화에 맞장구치는 답변을 친구처럼 한두 문장으로 해줘."
            )
            
            # 1차 시도: 제미나이 API 호출
            if ai_client:
                try:
                    response = ai_client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=prompt
                    )
                    await message.channel.send(str(response.text).strip())
                    return  # 성공하면 즉시 리턴하여 종료
                except Exception as gemini_error:
                    # 콘솔로그로 에러를 남겨두고 Groq 백업으로 진입
                    print(f"[Gemini Error] {gemini_error} -> Groq 엔진으로 전환합니다.")
            
            # 2차 시도 (Fallback): 제미나이 키가 없거나 호출 에러(크레딧 부족 등) 시 실행
            groq_response = await generate_with_groq(prompt)
            await message.channel.send(groq_response)

@tasks.loop(seconds=300) 
async def check_silence():
    global last_message_time, last_channel
    if datetime.now() - last_message_time > timedelta(seconds=SILENCE_TIMEOUT):
        target = bot.get_channel(TARGET_CHANNEL_ID) if TARGET_CHANNEL_ID else last_channel
        if target:
            last_message_time = datetime.now()
            prompt = (
                "너는 심심해진 디스코드 대화방에 먼저 말을 거는 친근하고 쾌활한 친구야.\n"
                "무조건 편한 반말로 '얘들아 뭐해?', '다들 자냐?' 같은 대화 주제를 딱 한 문장으로만 보내줘."
            )
            
            # 선톡 기능도 제미나이 실패 시 Groq으로 자동 연동
            if ai_client:
                try:
                    response = ai_client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=prompt
                    )
                    await target.send(str(response.text).strip())
                    return
                except Exception as gemini_error:
                    print(f"[Gemini Silence Loop Error] {gemini_error} -> Groq 전환")
            
            # 제미나이 오류 시 Groq 호출
            groq_response = await generate_with_groq(prompt)
            # 시스템 에러 문구(❌)가 아닐 때만 디스코드 채널에 선톡 전송
            if not groq_response.startswith("❌"):
                await target.send(groq_response)

bot.run(DISCORD_TOKEN)
