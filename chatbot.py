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
        "아래에 최근 대화 기록이 주어지면 그 흐름을 참고해서, 이미 나온 이야기를 기억하는 것처럼 자연스럽게 이어서 대답해.\n"
        "답변은 반드시 문장을 끝까지 완성해서 말해. 중간에 끊기지 않게 짧고 간결하게 요약해서라도 마무리해."
    )

# 라이브러리 파싱 버그를 원천 차단한 Groq 호출 함수
async def generate_with_groq(channel_id,
