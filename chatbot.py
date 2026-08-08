import discord
from discord.ext import tasks
from google import genai
from groq import AsyncGroq  # 공식 Groq 비동기 라이브러리 (405 에러 방지)
import asyncio
import os
from datetime import datetime, timedelta

# 수정된 Groq 비동기 함수 (한국어 꼬임 및 비속어 헛소리 방지)
async def generate_with_groq(prompt_content):
    """제미나이 한도 초과 시 Groq 공식 SDK를 통해 안정적으로 호출합니다."""
    if not groq_client:
        return "😭 제미나이 한도가 초과되었는데 백업 API 키(GROQ_API_KEY)도 등록되어 있지 않아."
        
    try:
        # System 역할을 명확히 분리하여 모델이 인성을 지키고 한글로만 말하도록 강제
        chat_completion = await groq_client.chat.completions.create(
            messages=[
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
            model="llama-3.3-70b-specdec",  # 더 안정적인 추론 모델로 변경
            temperature=0.6,                # 창의성을 살짝 낮춰 헛소리 확률 감소
            max_tokens=150                  # 답변이 너무 길어져 뇌절하는 것 방지
        )
        return chat_completion.choices.message.content.strip()
    except Exception as e:
        if "429" in str(e):
            return "😭 백업 엔진인 Groq 마저도 일시적인 한도 초과(429) 상태야. 잠시만 기다려줘!"
        return f"❌ 백업 엔진 에러 발생: {str(e)[:50]}"


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

# 백업 Groq 클라이언트 
if GROQ_API_KEY:
    groq_client = AsyncGroq(api_key=GROQ_API_KEY)
else:
    groq_client = None

last_message_time = datetime.now()
last_channel = None

# 수정된 Groq 비동기 함수 (모델명 에러 400 완벽 해결)
async def generate_with_groq(prompt_content):
    """제미나이 한도 초과 시 Groq 공식 SDK를 통해 안정적으로 호출합니다."""
    if not groq_client:
        return "😭 제미나이 한도가 초과되었는데 백업 API 키(GROQ_API_KEY)도 등록되어 있지 않아."
        
    try:
        # System 역할을 명확히 분리하여 모델이 인성을 지키고 한글로만 말하도록 강제
        chat_completion = await groq_client.chat.completions.create(
            messages=[
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
            model="llama-3.3-70b-versatile",  # 안정성이 검증된 표준 모델로 변경 완료
            temperature=0.6,                # 창의성을 살짝 낮춰 헛소리 확률 감소
            max_tokens=150                  # 답변이 너무 길어져 뇌절하는 것 방지
        )
        return chat_completion.choices.message.content.strip()
    except Exception as e:
        if "429" in str(e):
            return "😭 백업 엔진인 Groq 마저도 일시적인 한도 초과(429) 상태야. 잠시만 기다려줘!"
        return f"❌ 백업 엔진 에러 발생: {str(e)[:50]}"

@bot.event
async def on_ready():
    print(f'{bot.user} 봇 로그인 성공! (모델명 400 에러 해결 버전)')
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
            # 제미나이 전용 프롬프트 구조 유지
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
            
            # 2차 시도 (Fallback): 제미나이 오류 시 즉시 Groq 실행
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
            if not groq_response.startswith("❌"):
                await target.send(groq_response)

bot.run(DISCORD_TOKEN)
