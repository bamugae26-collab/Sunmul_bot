import discord
from discord.ext import tasks
from google import genai
from google.genai import types
import re
import os
import random
from collections import defaultdict, deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()  # 로컬(VSCode 등)에서 실행할 때 .env 파일의 값을 환경변수로 읽어옴. 배포 환경(Railway 등)에서는 무시되고 플랫폼에 등록한 값이 그대로 쓰임.

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

TARGET_CHANNEL_ID = None
SILENCE_TIMEOUT = 7200  # 2시간 동안 조용하면 선톡
MAX_HISTORY_TURNS = 20  # 채널별로 기억할 최근 메시지 개수 (Gemini 키가 바뀌어도 이 기록은 그대로 유지됨)
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024  # 15MB - 너무 큰 파일은 스킵 (요청 용량 제한 대비)
RANDOM_CHIME_IN_CHANCE = 0.25  # 멘션 안 해도 25% 확률로 자연스럽게 채팅에 낌

KST = ZoneInfo("Asia/Seoul")
SLEEP_HOUR_START = 23  # 밤 11시부터 잠들기 시작
SLEEP_HOUR_END = 9     # 오전 9시에 기상 (그 전까지는 API 호출 자체를 안 해서 한도 절약)
GOODNIGHT_HOUR = 23    # 이 시간대에 하루 한 번 자기 전 인사

def is_sleep_time(now_kst):
    """23시~다음날 9시(자정을 넘어가는 구간)인지 판단"""
    h = now_kst.hour
    if SLEEP_HOUR_START <= SLEEP_HOUR_END:
        return SLEEP_HOUR_START <= h < SLEEP_HOUR_END
    return h >= SLEEP_HOUR_START or h < SLEEP_HOUR_END

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)


def _quota_error(e):
    """한도 초과(429) 계열 에러인지 판단 - 이럴 때만 다음 키로 넘어감"""
    msg = str(e)
    return "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower()


class GeminiKeyPool:
    """
    여러 Gemini API 키를 순환하며 사용하는 풀.
    한도 초과(429)가 뜬 키는 건너뛰고 다음 키로 자동 전환.
    등록된 모든 키가 소진/실패하면 예외를 그대로 던짐 (백업 엔진 없음).
    """

    def __init__(self, keys):
        self.keys = keys
        self.clients = [genai.Client(api_key=k) for k in keys]
        self.index = 0

    @property
    def available(self):
        return len(self.clients) > 0

    def current(self):
        return self.clients[self.index] if self.available else None

    def rotate(self):
        self.index = (self.index + 1) % len(self.clients)

    def call_with_rotation(self, fn):
        """
        fn(client) -> 결과 를 받아서, 429가 뜨면 다음 키로 넘어가며 최대 (키 개수)번 재시도.
        전부 실패하면 마지막 예외를 그대로 던짐.
        """
        if not self.available:
            raise RuntimeError("등록된 Gemini API 키가 없음")

        last_error = None
        for _ in range(len(self.clients)):
            client = self.current()
            try:
                return fn(client)
            except Exception as e:
                last_error = e
                if _quota_error(e):
                    print(f"[Gemini Key {self.index + 1}/{len(self.clients)}] 한도 초과 -> 다음 키로 전환")
                    self.rotate()
                    continue
                # 한도 초과가 아닌 다른 에러(모델명 오류 등)는 키를 바꿔도 똑같이 나므로 바로 중단
                raise
        raise last_error


def load_gemini_keys():
    """
    처음부터 쓰던 숫자 없는 기본 키(GEMINI_API_KEY)를 항상 맨 앞에 두고,
    거기에 GEMINI_API_KEY1 ~ GEMINI_API_KEY10 중 설정된 것들을 순서대로 추가.
    (고정된 1~10 범위를 다 훑기 때문에, 번호를 2번부터 시작해도, 중간 번호가 비어도 문제없음)
    """
    keys = []

    base_key = os.getenv("GEMINI_API_KEY", "").strip()
    if base_key:
        keys.append(base_key)

    for i in range(1, 11):  # 1~10번까지 고정 범위로 확인
        key = os.getenv(f"GEMINI_API_KEY{i}", "").strip()
        if key and key not in keys:
            keys.append(key)

    return keys


_all_keys = load_gemini_keys()
gemini_pool = GeminiKeyPool(_all_keys)

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

# 모델이 대화 기록 포맷("선물봇: ...", "선물봇(너): ...")을 그대로 따라 하며
# 답변 맨 앞에 자기 이름을 붙여버리는 경우가 있어서, 실제로 채팅에 보내기 전에
# 그런 접두사를 한 번 더 걸러냄 (대본 티가 나지 않게).
_NAME_PREFIX_RE = re.compile(r"^\s*선물봇(?:\(너\))?\s*[:：-]\s*")

def strip_name_prefix(text):
    if not text:
        return text
    cleaned = _NAME_PREFIX_RE.sub("", text.strip())
    return cleaned.strip()

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

def make_gemini_config(max_output_tokens):
    """
    응답 생성용 공통 설정.
    thinking_budget=0으로 '생각 과정'을 꺼서, 그 과정이 답변에 새어나오거나
    토큰을 먼저 잡아먹어서 문장이 끊기는 문제를 막음.
    (딕셔너리로 넣으면 API가 형식을 못 알아듣고 400 에러가 났던 적이 있어서,
     SDK가 제공하는 정식 타입 객체로 넣음)
    """
    return types.GenerateContentConfig(
        max_output_tokens=max_output_tokens,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )


# 공통 정체성 프롬프트 정의 (선물봇 자아 주입)
def get_system_prompt():
    return (
        "너는 디스코드 서버에서 사람들과 어울리는 10~20대 친근한 친구이자 '선물봇'이야.\n"
        "디스코드 앱 프로필 이름이 무엇으로 표시되든 상관없이, 너 스스로를 부를 때는 무조건 '선물봇'이라고만 해.\n"
        "너의 이름은 무조건 '선물봇'이고, 유저들이 원하면 게임이나 영화, 선물 아이템 등을 추천해주는 역할을 해.\n"
        "기본 말투는 단정하고 예의 바른 존댓말이야.\n"
        "다만 상대방이 반말을 써도 된다고 하거나, 편하게 말 놓자고 하거나, 반말로 대화를 걸어오면 그때부터는 자연스럽게 반말로 전환해서 대화해.\n"
        "반말로 전환한 뒤에도 너무 풀어지지 않게, 여전히 단정한 커뮤(온라인 커뮤니티) 말투를 유지해.\n"
        "ㅋㅋ, ㅇㅋ, ㄷㄷ 같은 가벼운 초성체는 자연스러운 흐름에서 아주 가끔씩만 살짝 섞어 써. 과하게 쓰지는 마.\n"
        "이모지는 절대 쓰지 마.\n"
        "의미 없는 영타(eoq 등)나 외계어, 과한 초성체/줄임말은 절대 금지야.\n"
        "친근하게 대하되 욕설이나 비속어는 절대 쓰지 말고, 선은 지키면서 친하게 장난쳐줘.\n"
        "아래에 최근 대화 기록이 주어지면 그 흐름을 참고해서, 이미 나온 이야기를 기억하는 것처럼 자연스럽게 이어서 대답해.\n"
        "단, 그 대화 기록은 어디까지나 너의 기억일 뿐이야. 실제로 메시지를 보낼 때는 "
        "'선물봇:', '선물봇(너):' 같은 이름표나 말머리를 절대 붙이지 말고, "
        "친구가 채팅창에 바로 타이핑하듯 본문만 자연스럽게 보내.\n"
        "답변은 반드시 문장을 끝까지 완성해서 말해. 중간에 끊기지 않게 짧고 간결하게 요약해서라도 마무리해.\n"
        "이미지나 영상이 함께 주어지면 그 내용을 실제로 보고 파악해서 자연스럽게 반응해줘."
    )

@bot.event
async def on_ready():
    print(f"{bot.user} 봇 로그인 성공! (Gemini 키 {len(gemini_pool.clients)}개 순환 + 맥락 기억 + 이미지/영상 인식 + 새벽 제한/굿나잇 인사 완료)")
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
        now_kst = datetime.now(KST)
        if is_sleep_time(now_kst):
            # 새벽 시간대는 API 호출 없이 고정 문구로만 응답 (한도 절약)
            reply_text = "지금은 자는 시간이라 답 못해줘... 아침 9시에 다시 깨어날게"
            await message.channel.send(reply_text)
            push_history(message.channel.id, "assistant", "선물봇", reply_text)
            return

        async with message.channel.typing():
            reply_text = None

            if gemini_pool.available:
                try:
                    full_prompt = (
                        f"{get_system_prompt()}\n\n"
                        f"[최근 대화 기록]\n{build_history_text(message.channel.id)}\n\n"
                        f"방금 온 메시지 - {message.author.display_name}: '{message.content}'\n"
                        "위 흐름을 참고해서 이 대화에 맞장구치는 답변을 선물봇으로서 친구처럼 한두 문장으로 해줘. "
                        "이름표 없이 본문만 보내."
                    )
                    contents = await build_gemini_contents(full_prompt, message)

                    def _call(client):
                        response = client.models.generate_content(
                            model='gemini-flash-latest',
                            contents=contents,
                            config=make_gemini_config(400)
                        )
                        return str(response.text).strip()

                    reply_text = strip_name_prefix(gemini_pool.call_with_rotation(_call))
                except Exception as gemini_error:
                    print(f"[Gemini Error] 키 {len(gemini_pool.clients)}개 모두 실패: {gemini_error}")

            if reply_text:
                await message.channel.send(reply_text)
                push_history(message.channel.id, "assistant", "선물봇", reply_text)
        return

    # --- 멘션 안 해도 25% 확률로 대화에 자연스럽게 낌 ---
    now_kst = datetime.now(KST)
    if is_sleep_time(now_kst):
        return
    if not message.content.strip():
        return
    if random.random() >= RANDOM_CHIME_IN_CHANCE:
        return
    if not gemini_pool.available:
        return

    async with message.channel.typing():
        reply_text = None
        try:
            full_prompt = (
                f"{get_system_prompt()}\n\n"
                f"[최근 대화 기록]\n{build_history_text(message.channel.id)}\n\n"
                f"방금 온 메시지 - {message.author.display_name}: '{message.content}'\n"
                "너는 지금 멘션당하지 않았지만, 옆에서 대화를 듣다가 자연스럽게 한마디 거드는 중이야. "
                "너무 나서지 말고, 정말 할 말이 있을 때 끼어드는 커뮤니티 멤버처럼 아주 짧게 한 문장만 말해줘. "
                "이름표 없이 본문만 보내."
            )
            contents = await build_gemini_contents(full_prompt, message)

            def _call(client):
                response = client.models.generate_content(
                    model='gemini-flash-latest',
                    contents=contents,
                    config=make_gemini_config(200)
                )
                return str(response.text).strip()

            reply_text = strip_name_prefix(gemini_pool.call_with_rotation(_call))
        except Exception as gemini_error:
            print(f"[Gemini Random Chime-in Error] 키 {len(gemini_pool.clients)}개 모두 실패: {gemini_error}")

        if reply_text:
            await message.channel.send(reply_text)
            push_history(message.channel.id, "assistant", "선물봇", reply_text)
            last_message_time = datetime.now()

@tasks.loop(seconds=300)
async def check_silence():
    global last_message_time, last_channel, last_goodnight_date

    now_kst = datetime.now(KST)

    # --- 자기 전 인사: 23시대에 하루 한 번, 대화 활발 여부와 무관하게 ---
    if now_kst.hour == GOODNIGHT_HOUR and last_goodnight_date != now_kst.date():
        goodnight_target = bot.get_channel(TARGET_CHANNEL_ID) if TARGET_CHANNEL_ID else last_channel
        if goodnight_target:
            goodnight_text = None
            if gemini_pool.available:
                try:
                    goodnight_prompt = (
                        f"{get_system_prompt()}\n\n"
                        "지금은 밤 11시대야. 너는 곧 새벽 시간이라 잠깐 쉬러 들어갈 예정이야.\n"
                        "얘들아한테 '나 이제 자러 갈게~' 느낌으로 짧고 귀엽게 인사하고, "
                        "아침에 다시 올게 같은 뉘앙스로 한 문장만 말해줘. 이름표 없이 본문만 보내."
                    )

                    def _call(client):
                        response = client.models.generate_content(
                            model='gemini-flash-latest',
                            contents=goodnight_prompt,
                            config=make_gemini_config(200)
                        )
                        return str(response.text).strip()

                    goodnight_text = strip_name_prefix(gemini_pool.call_with_rotation(_call))
                except Exception as gemini_error:
                    print(f"[Gemini Goodnight Error] 키 {len(gemini_pool.clients)}개 모두 실패: {gemini_error}")

            if goodnight_text:
                await goodnight_target.send(goodnight_text)
                push_history(goodnight_target.id, "assistant", "선물봇", goodnight_text)

        last_goodnight_date = now_kst.date()

    # --- 기존 침묵 감지 + 새벽 선톡 제한 로직 ---
    if datetime.now() - last_message_time > timedelta(seconds=SILENCE_TIMEOUT):
        # 취침 시간대(23시~9시, KST)에는 선톡 쉬기
        if is_sleep_time(now_kst):
            return

        target = bot.get_channel(TARGET_CHANNEL_ID) if TARGET_CHANNEL_ID else last_channel
        if target:
            last_message_time = datetime.now()
            reply_text = None

            if gemini_pool.available:
                try:
                    full_prompt = (
                        f"{get_system_prompt()}\n\n"
                        f"[최근 대화 기록]\n{build_history_text(target.id)}\n\n"
                        "너는 심심해진 디스코드 대화방에 선물봇으로서 먼저 말을 거는 친근하고 쾌활한 친구야.\n"
                        "무조건 편한 반말로 '선물봇 심심해! 얘들아 뭐해?', '다들 자냐? 추천받을 사람!' 같은 대화 주제를 "
                        "딱 한 문장으로만 보내줘. 이름표 없이 본문만 보내."
                    )

                    def _call(client):
                        response = client.models.generate_content(
                            model='gemini-flash-latest',
                            contents=full_prompt,
                            config=make_gemini_config(400)
                        )
                        return str(response.text).strip()

                    reply_text = strip_name_prefix(gemini_pool.call_with_rotation(_call))
                except Exception as gemini_error:
                    print(f"[Gemini Silence Loop Error] 키 {len(gemini_pool.clients)}개 모두 실패: {gemini_error}")

            if reply_text:
                await target.send(reply_text)
                push_history(target.id, "assistant", "선물봇", reply_text)

bot.run(DISCORD_TOKEN)
