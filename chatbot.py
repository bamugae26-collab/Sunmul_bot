import discord
from discord.ext import tasks
from google import genai
import asyncio
import aiohttp  # 라이브러리 에러 꼬임을 막기 위한 비동기 다이렉트 통신 라이브러리
import json
import os
from datetime import datetime, timedelta

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()  

TARGET_CHANNEL_ID = None  
SILENCE_TIMEOUT = 1800 

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

# 메인 제미나이 클라이언트
if GEMINI_API_KEY:
    ai_client = genai.Client(api_key=GEMINI_API_KEY)
else:
    ai_client = None

last_message_time = datetime.now()
last_channel = None

# 라이브러리 의존성을 완전히 제거하고 주소로 직접 찌르는 Groq 비동기 함수
async def generate_with_groq(prompt_content):
    """라이브러리 충돌 및 400 에러를 우회하여 Groq API에 다이렉트 POST 요청을 전송합니다."""
    if not GROQ_API_KEY:
        return "😭 제미나이 한도가 초과되었는데 백업 API 키(GROQ_API_KEY)도 등록되어 있지 않아."
        
    # Groq 표준 REST API 엔드포인트와 헤더 직접 구성
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "llama-3.3-70b-versatile",  # 현재 Groq 플랫폼 메인 활성화 프로덕션 모델
        "messages": [
            {
                "role": "system",
                "content": (
                    "너는 디스코드 서버에서 사람들과 어울리는 장난기 많고 친근한 10~20대 친구야.\n"
                    "무조건 100% 편한 한국어 반말만 사용해라. 의미 없는 영타(eoq 등)나 외계어는 절대 금지야.\n"
                    "친근하게 대하되 욕설, 비속어, 모욕적인 표현은 절대 쓰지 마. 선은 지키면서 장난쳐줘."
                )
            },
            {
                "role": "user",
                "content": f"상대방 내용: '{prompt_content}'\n이 대화에 맞장구치는 답변을 친구처럼 한두 문장으로 해줘."
            }
        ],
        "temperature": 0.6,
        "max_tokens": 150
    }
    
    try:
        # 안전장치 타임아웃 10초 부여
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    result = await response.json()
                    return result["choices"][0]["message"]["content"].strip()
                else:
                    # 실패 시 Groq 서버가 반환한 원본 에러 코드 확인용 메시지
                    res_body = await response.text()
                    print(f"[Groq Direct API Debug Log] Status: {response.status}, Body: {res_body}")
                    
                    if response.status == 401:
                        return "❌ [Groq 오류] API 키 인증 실패(401). Railway Variables 대시보드에 키가 똑바로 입력됐는지 확인해줘!"
                    if response.status == 429:
                        return "😭 백업 엔진인 Groq 마저도 일시적인 분당 트래픽 제한(429) 상태야. 잠시만 기다려줘!"
                    return f"❌ 백업 서버 응답 신호 거절 (HTTP 에러 코드: {response.status})"
                    
    except Exception as e:
        print(f"[Groq Network Exception] {str(e)}")
        return "❌ 백업 서버와 연결에 실패했습니다. (네트워크 지연)"

@bot.event
async def on_ready():
    print(f'{bot.user} 봇 로그인 성공! (다이렉트 우회 웹패치 적용 완료)')
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
                    return  
                except Exception as gemini_error:
                    print(f"[Gemini Error] {gemini_error} -> Groq 엔진으로 전환합니다.")
            
            # 2차 시도 (Fallback): 제미나이 오류 시 즉시 Groq 다이렉트 실행
            groq_response = await generate_with_groq(message.content)
            await message.channel.send(groq_response)

@tasks.loop(seconds=300) 
async def check_silence():
    global last_message_time, last_channel
    if datetime.now() - last_message_time > timedelta(seconds=SILENCE_TIMEOUT):
        target = bot.get_channel(TARGET_CHANNEL_ID) if TARGET_CHANNEL_ID else last_channel
        if target:
            last_message_time = datetime.now()
            
            if ai_client:
                try:
                    prompt = (
                        "너는 심심해진 디스코드 대화방에 먼저 말을 거는 친근하고 쾌활한 친구야.\n"
                        "무조건 편한 반말로 '얘들아 뭐해?', '다들 자냐?' 같은 대화 주제를 딱 한 문장으로만 보내줘."
                    )
                    response = ai_client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=prompt
                    )
                    await target.send(str(response.text).strip())
                    return
                except Exception as gemini_error:
                    print(f"[Gemini Silence Loop Error] {gemini_error} -> Groq 전환")
            
            # 제미나이 실패 시 Groq 선톡 작동
            groq_response = await generate_with_groq("심심한 대화방에 선톡 날려줘")
            if not groq_response.startswith("❌") and not groq_response.startswith("😭"):
                await target.send(groq_response)

bot.run(DISCORD_TOKEN)
