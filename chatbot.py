import discord
from discord.ext import tasks
from google import genai
from google.genai import types
from openai import AsyncOpenAI  
import asyncio
import os
from collections import defaultdict, deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()  

TARGET_CHANNEL_ID = None  
SILENCE_TIMEOUT = 1800
MAX_HISTORY_TURNS = 12  # 채널별로 기억할 최근 메시지 개수
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024  # 15MB - 너무 큰 파일은 스킵 (요청 용량 제한 대비)

KST = ZoneInfo("Asia/Seoul")
QUIET_HOUR_START = 0   # 밤 12시
QUIET_HOUR_END = 6     # 오전 6시
GOODNIGHT_HOUR = 23    # 이 시간대에 하루 한 번 자기 전 인사

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
last_goodnight_date = None  # 오늘 자기 전 인사를 이미 했는지 추적

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

def build_groq_messages(channel_id, current_user_name, current_content, has_attachment=False):
    """Groq(OpenAI 호환)용 - messages 배열 형태로 정리. Groq는 이미지/영상을 못 보므로 텍스트만 전달."""
    messages = [{"role": "system", "content": get_system_prompt()}]
    for h in channel_history[channel_id]:
        if h["role"] == "assistant":
            messages.append({"role": "assistant", "content": h["content"]})
        else:
            messages.append({"role": "user", "content": f"{h['name']}: {h['content']}"})

    user_line = f"{current_user_name}: {current_content}"
    if has_attachment:
        user_line += "\n(이 메시지에 이미지 또는 영상이 첨부되어 있지만, 지금 답변 엔진은 파일을 볼 수 없어. 파일을 못 본다는 걸 자연스럽게 언급하며 답해줘.)"
    messages.append({"role": "user", "content": user_line})
    return messages

async def build_gemini_contents(prompt_text, message):
    """첨부된 이미지/영상을 Gemini 멀티모달 입력으로 변환"""
    parts = [prompt_text]
    for attachment in message.attachments:
        content_type = attachment.content_type or ""
        if not (content_type.startswith("image/") or content_type.startswith("video/")):
            continue
        if attachment.size and attachment.size > MAX_ATTACHMENT_BYTES:
            parts[0] += f"\n(참고: {attachment.filename} 파일이 너무 커서 직접 보지는 못했어)"
            continue
        try:
            file_bytes = await attachment.read()
            parts.append(types.Part.from_bytes(data=file_bytes, mime_type=content_type))
        except Exception as e:
            print(f"[Attachment Read Error] {e}")
    return parts

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
        "답변은 반드시 문장을 끝까지 완성해서 말해. 중간에 끊기지 않게 짧고 간결하게 요약해서라도 마무리해.\n"
        "이미지나 영상이 함께 주어지면 그 내용을 실제로 보고 파악해서 자연스럽게 반응해줘."
    )

# 라이브러리 파싱 버그를 원천 차단한 Groq 호출 함수
async def generate_with_groq(channel_id, current_user_name, prompt_content, has_attachment=False):
    """OpenAI 비동기 표준 SDK 구조를 이용하되, 딕셔너리 안전 분해 방식으로 대답을 파싱합니다."""
    if not groq_client:
        return "😭 제미나이 한도가 초과되었는데 백업 API 키(GROQ_API_KEY)도 등록되어 있지 않아."
        
    try:
        chat_completion = await groq_client.chat.completions.create(
            messages=build_groq_messages(channel_id, current_user_name, prompt_content, has_attachment),
            model="openai/gpt-oss-120b",
            temperature=0.6,
            max_tokens=300
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
    print(f'{bot.user} 봇 로그인 성공! (맥락 기억 + 정체성 주입 + 답변 완결성 + 이미지/영상 인식 + 새벽 제한/굿나잇 인사 완료)')
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
    has_attachment = len(message.attachments) > 0

    # 봇을 부르지 않은 메시지도 맥락 파악용으로 기록만 해둠
    history_note = message.content + (" [첨부파일 있음]" if has_attachment else "")
    push_history(message.channel.id, "user", message.author.display_name, history_note)

    if is_called:
        async with message.channel.typing():
            reply_text = None

            # 1차 시도: 제미나이 API 호출 (이미지/영상 첨부 시 함께 전달)
            if ai_client:
                try:
                    full_prompt = (
                        f"{get_system_prompt()}\n\n"
                        f"[최근 대화 기록]\n{build_history_text(message.channel.id)}\n\n"
                        f"방금 온 메시지 - {message.author.display_name}: '{message.content}'\n"
                        "위 흐름을 참고해서 이 대화에 맞장구치는 답변을 선물봇으로서 친구처럼 한두 문장으로 해줘."
                    )
                    contents = await build_gemini_contents(full_prompt, message)
                    response = ai_client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=contents,
                        config={"max_output_tokens": 400}
                    )
                    reply_text = str(response.text).strip()
                except Exception as gemini_error:
                    print(f"[Gemini Error] {gemini_error} -> Groq 엔진으로 전환합니다.")

            # 2차 시도 (Fallback): 제미나이 오류 시 즉시 Groq 실행 (이미지는 못 봄)
            if reply_text is None:
                reply_text = await generate_with_groq(
                    message.channel.id, message.author.display_name, message.content, has_attachment
                )

            await message.channel.send(reply_text)
            push_history(message.channel.id, "assistant", "선물봇", reply_text)

@tasks.loop(seconds=300) 
async def check_silence():
    global last_message_time, last_channel, last_goodnight_date

    now_kst = datetime.now(KST)

    # --- 자기 전 인사: 23시대에 하루 한 번, 대화 활발 여부와 무관하게 ---
    if now_kst.hour == GOODNIGHT_HOUR and last_goodnight_date != now_kst.date():
        goodnight_target = bot.get_channel(TARGET_CHANNEL_ID) if TARGET_CHANNEL_ID else last_channel
        if goodnight_target:
            goodnight_text = None
            if ai_client:
                try:
                    goodnight_prompt = (
                        f"{get_system_prompt()}\n\n"
                        "지금은 밤 11시대야. 너는 곧 새벽 시간이라 잠깐 쉬러 들어갈 예정이야.\n"
                        "얘들아한테 '나 이제 자러 갈게~' 느낌으로 짧고 귀엽게 인사하고, "
                        "아침에 다시 올게 같은 뉘앙스로 한 문장만 말해줘."
                    )
                    response = ai_client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=goodnight_prompt,
                        config={"max_output_tokens": 200}
                    )
                    goodnight_text = str(response.text).strip()
                except Exception as gemini_error:
                    print(f"[Gemini Goodnight Error] {gemini_error} -> Groq 전환")

            if goodnight_text is None:
                goodnight_text = await generate_with_groq(
                    goodnight_target.id, "선물봇", "곧 새벽이라 잠깐 자러 간다고 짧게 인사해줘"
                )

            if goodnight_text and not goodnight_text.startswith("❌") and not goodnight_text.startswith("😭"):
                await goodnight_target.send(goodnight_text)
                push_history(goodnight_target.id, "assistant", "선물봇", goodnight_text)

        last_goodnight_date = now_kst.date()

    # --- 기존 침묵 감지 + 새벽 선톡 제한 로직 ---
    if datetime.now() - last_message_time > timedelta(seconds=SILENCE_TIMEOUT):
        # 새벽 0시~6시(KST)에는 선톡 쉬기
        if QUIET_HOUR_START <= now_kst.hour < QUIET_HOUR_END:
            return

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
                        contents=full_prompt,
                        config={"max_output_tokens": 400}
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
