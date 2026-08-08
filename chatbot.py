import discord
from discord.ext import tasks
from google import genai
from openai import AsyncOpenAI  
import asyncio
import os
from collections import defaultdict, deque
from datetime import datetime, timedelta

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()  

TARGET_CHANNEL_ID = None  
SILENCE_TIMEOUT = 1800
MAX_HISTORY_TURNS = 12  # 채널별로 기억할 최근 메시지 개수

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
        base_url="https://api.groq.com/openai/v1"
    )
else:
    groq_client = None

last_message_time = datetime.now()
last_channel = None

# 채널별 최근 대화 기록 (맥락 파악용)
channel_history = defaultdict(lambda: deque(maxlen=MAX_HISTORY_TURNS))

def push_history(channel_id, role, name, content):
    """role: 'user' 또는 'assistant'"""
    channel_history[channel_id].append({"role": role, "name": name, "content": content})

def build_history_text(channel_id):
    """제미나이용 - 대화 기록을 사람이 읽는 텍스트로 정리"""
    lines = []
    for h in channel_history[channel_id]:
        speaker = "선물봇(너)" if h["role"] == "assistant" else h["name"]
        lines.append(f"{speaker}: {h['content']}")
    return "\n".join(lines) if lines else "(아직 대화 기록 없음)"

def build_groq_messages(channel_id, current_user_name, current_content):
    """Groq(OpenAI 호환)용 - messages 배열 형태로 정리"""
    messages = [{"role": "system", "content": get_system_prompt()}]
    for h in channel_history[channel_id]:
        if h["role"] == "assistant":
            messages.append({"role": "assistant", "content": h["content"]})
        else:
            messages.append({"role": "user", "content": f"{h['name']}: {h['content']}"})
    messages.append({"role": "user", "content": f"{current_user_name}: {current_content}"})
    return messages

# 공통 정체성 프롬프트 정의 (선물봇 자아 주입)
def get_system_prompt():
    return (
        "너는 디스코드 서버에서 사람들과 어울리는 10~20대 친근한 친구이자 '선물봇'이야.\n"
        "디스코드 앱 프로필 이름이 무엇으로 표시되든 상관없이, 너 스스로를 부를 때는 무조건 '선물봇'이라고만 해.\n"
        "너의 이름은 무조건 '선물봇'이고, 유저들이 원하면 게임이나 영화, 선물 아이템 등을 추천해주는 역할을 해.\n"
        "절대 존댓말을 쓰지 말고 100% 편한 한국어 반말만 사용해라. 이모지도 섞어줘.\n"
        "의미 없는 영타(eoq 등)나 외계어는 절대 금지야.\n"
        "친근하게 대하되 욕설이나 비속어는 절대 쓰지 말고, 선은 지키면서 친하게 장난쳐줘.\n"
        "아래에 최근 대화 기록이 주어지면 그 흐름을 참고해서, 이미 나온 이야기를 기억하는 것처럼 자연스럽게 이어서 대답해."
    )

# 라이브러리 파싱 버그를 원천 차단한 Groq 호출 함수
async def generate_with_groq(channel_id, current_user_name, prompt_content):
    """OpenAI 비동기 표준 SDK 구조를 이용하되, 딕셔너리 안전 분해 방식으로 대답을 파싱합니다."""
    if not groq_client:
        return "😭 제미나이 한도가 초과되었는데 백업 API 키(GROQ_API_KEY)도 등록되어 있지 않아."
        
    try:
        chat_completion = await groq_client.chat.completions.create(
            messages=build_groq_messages(channel_id, current_user_name, prompt_content),
            model="openai/gpt-oss-120b",
            temperature=0.6,
            max_tokens=150
        )
        
        try:
            return chat_completion.choices[0].message.content.strip()
        except:
            res_dict = chat_completion.model_dump()
            return res_dict['choices'][0]['message']['content'].strip()
            
    except Exception as e:
        error_msg = str(e)
        print(f"[Groq Client Fatal Error] {error_msg}")
        
        if "429" in error_msg:
            return "😭 백업 엔진인 Groq 마저도 일시적인 한도 초과(429) 상태야. 잠시만 기다려줘!"
        if "401" in error_msg:
            return "❌ [Groq 오류] API 키 인증에 실패했어. Railway 환경변수의 GROQ_API_KEY 값을 다시 확인해줘!"
            
        return f"❌ 백업 서버 통신 장치 충돌 발생! (원인: {error_msg[:30]})"

@bot.event
async def on_ready():
    print(f'{bot.user} 봇 로그인 성공! (맥락 기억 + 정체성 주입 완료)')
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

    # Bot name / App name 모두 "선물봇"으로 통일되어 있으므로 아래 조건으로 충분함
    is_called = bot.user.mentioned_in(message) or ("선물봇" in message.content)

    # 봇을 부르지 않은 메시지도 맥락 파악용으로 기록만 해둠
    push_history(message.channel.id, "user", message.author.display_name, message.content)

    if is_called:
        async with message.channel.typing():
            reply_text = None

            # 1차 시도: 제미나이 API 호출
            if ai_client:
                try:
                    full_prompt = (
                        f"{get_system_prompt()}\n\n"
                        f"[최근 대화 기록]\n{build_history_text(message.channel.id)}\n\n"
                        f"방금 온 메시지 - {message.author.display_name}: '{message.content}'\n"
                        "위 흐름을 참고해서 이 대화에 맞장구치는 답변을 선물봇으로서 친구처럼 한두 문장으로 해줘."
                    )
                    response = ai_client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=full_prompt
                    )
                    reply_text = str(response.text).strip()
                except Exception as gemini_error:
                    print(f"[Gemini Error] {gemini_error} -> Groq 엔진으로 전환합니다.")

            # 2차 시도 (Fallback): 제미나이 오류 시 즉시 Groq 실행
            if reply_text is None:
                reply_text = await generate_with_groq(
                    message.channel.id, message.author.display_name, message.content
                )

            await message.channel.send(reply_text)
            push_history(message.channel.id, "assistant", "선물봇", reply_text)

@tasks.loop(seconds=300) 
async def check_silence():
    global last_message_time, last_channel
    if datetime.now() - last_message_time > timedelta(seconds=SILENCE_TIMEOUT):
        target = bot.get_channel(TARGET_CHANNEL_ID) if TARGET_CHANNEL_ID else last_channel
        if target:
            last_message_time = datetime.now()
            reply_text = None
            
            if ai_client:
                try:
                    full_prompt = (
                        f"{get_system_prompt()}\n\n"
                        f"[최근 대화 기록]\n{build_history_text(target.id)}\n\n"
                        "너는 심심해진 디스코드 대화방에 선물봇으로서 먼저 말을 거는 친근하고 쾌활한 친구야.\n"
                        "무조건 편한 반말로 '선물봇 심심해! 얘들아 뭐해?', '다들 자냐? 추천받을 사람!' 같은 대화 주제를 딱 한 문장으로만 보내줘."
                    )
                    response = ai_client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=full_prompt
                    )
                    reply_text = str(response.text).strip()
                except Exception as gemini_error:
                    print(f"[Gemini Silence Loop Error] {gemini_error} -> Groq 전환")

            if reply_text is None:
                reply_text = await generate_with_groq(target.id, "선물봇", "심심한 대화방에 선물봇으로서 선톡 날려줘")

            if reply_text and not reply_text.startswith("❌") and not reply_text.startswith("😭"):
                await target.send(reply_text)
                push_history(target.id, "assistant", "선물봇", reply_text)

bot.run(DISCORD_TOKEN)
