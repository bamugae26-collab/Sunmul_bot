import discord
from discord.ext import tasks
from google import genai
from openai import AsyncOpenAI  
import asyncio
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

# 백업 Groq 클라이언트
if GROQ_API_KEY:
    groq_client = AsyncOpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://groq.com"  
    )
else:
    groq_client = None

last_message_time = datetime.now()
last_channel = None

# 공통 정체성 프롬프트 정의 (선물봇 자아 주입)
def get_system_prompt():
    return (
        "너는 디스코드 서버에서 사람들과 어울리는 10~20대 친근한 친구이자 '선물봇'이야.\n"
        "너의 이름은 무조건 '선물봇'이고, 유저들이 원하면 게임이나 영화, 선물 아이템 등을 추천해주는 역할을 해.\n"
        "절대 존댓말을 쓰지 말고 100% 편한 한국어 반말만 사용해라. 이모지도 섞어줘.\n"
        "의미 없는 영타(eoq 등)나 외계어는 절대 금지야.\n"
        "친근하게 대하되 욕설이나 비속어는 절대 쓰지 말고, 선은 지키면서 친하게 장난쳐줘."
    )

# 라이브러리 파싱 버그를 원천 차단한 Groq 호출 함수
async def generate_with_groq(prompt_content):
    """OpenAI 비동기 표준 SDK 구조를 이용하되, 딕셔너리 안전 분해 방식으로 대답을 파싱합니다."""
    if not groq_client:
        return "😭 제미나이 한도가 초과되었는데 백업 API 키(GROQ_API_KEY)도 등록되어 있지 않아."
        
    try:
        chat_completion = await groq_client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": get_system_prompt()
                },
                {
                    "role": "user",
                    "content": f"상대방 내용: '{prompt_content}'\n이 대화에 맞장구치는 답변을 선물봇으로서 친구처럼 한두 문장으로 해줘."
                }
            ],
            model="llama-3.3-70b-specdec",  # 그록 메인 서비스 가동 모델 주소 지정
            temperature=0.6,
            max_tokens=150
        )
        
        # [핵심 수리] 객체 접근법과 딕셔너리 접근법을 둘 다 엮어 파싱 버그 원천 차단
        try:
            return chat_completion.choices[0].message.content.strip()
        except:
            # 만약 객체 접근이 실패하면 딕셔너리 모델로 강제 덤프 후 안전 추출
            res_dict = chat_completion.model_dump()
            return res_dict['choices'][0]['message']['content'].strip()
            
    except Exception as e:
        error_msg = str(e)
        print(f"[Groq Client Fatal Error] {error_msg}")  # 레일웨이 터미널에서 확인 가능한 진짜 에러
        
        if "429" in error_msg:
            return "😭 백업 엔진인 Groq 마저도 일시적인 한도 초과(429) 상태야. 잠시만 기다려줘!"
        if "401" in error_msg:
            return "❌ [Groq 오류] API 키 인증에 실패했어. Railway 환경변수의 GROQ_API_KEY 값을 다시 확인해줘!"
            
        return f"❌ 백업 서버 통신 장치 충돌 발생! (원인: {error_msg[:30]})"

@bot.event
async def on_ready():
    print(f'{bot.user} 봇 로그인 성공! (파싱 버그 최종 수리 완려)')
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
            # 1차 시도: 제미나이 API 호출
            if ai_client:
                try:
                    full_prompt = (
                        f"{get_system_prompt()}\n\n"
                        f"상대방 내용: '{message.content}'\n"
                        "이 대화에 맞장구치는 답변을 선물봇으로서 친구처럼 한두 문장으로 해줘."
                    )
                    response = ai_client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=full_prompt
                    )
                    await message.channel.send(str(response.text).strip())
                    return  
                except Exception as gemini_error:
                    print(f"[Gemini Error] {gemini_error} -> Groq 엔진으로 전환합니다.")
            
            # 2차 시도 (Fallback): 제미나이 오류 시 즉시 OpenAI 구조화 Groq 실행
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
                    full_prompt = (
                        f"{get_system_prompt()}\n\n"
                        "너는 심심해진 디스코드 대화방에 선물봇으로서 먼저 말을 거는 친근하고 쾌활한 친구야.\n"
                        "무조건 편한 반말로 '선물봇 심심해! 얘들아 뭐해?', '다들 자냐? 추천받을 사람!' 같은 대화 주제를 딱 한 문장으로만 보내줘."
                    )
                    response = ai_client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=full_prompt
                    )
                    await target.send(str(response.text).strip())
                    return
                except Exception as gemini_error:
                    print(f"[Gemini Silence Loop Error] {gemini_error} -> Groq 전환")
            
            # 제미나이 실패 시 Groq 선톡 작동
            groq_response = await generate_with_groq("심심한 대화방에 선물봇으로서 선톡 날려줘")
            if not groq_response.startswith("❌") and not groq_response.startswith("😭"):
                await target.send(groq_response)

bot.run(DISCORD_TOKEN)
