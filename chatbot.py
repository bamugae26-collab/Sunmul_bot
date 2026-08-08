import discord
from discord.ext import tasks
from google import genai
import asyncio
from datetime import datetime, timedelta

# ================= 설정 구간 =================
DISCORD_TOKEN = "여기에_디스코드_봇_토큰_입력"
GEMINI_API_KEY = "여기에_구글_제미나이_API_키_입력"

# 대화가 없을 때 선톡을 보낼 디스코드 채널 ID (숫자 18~19자리)
TARGET_CHANNEL_ID = None  # 예: 123456789012345678 (따옴표 없이 숫자만)

# 몇 초 동안 말이 없으면 선톡을 보낼지 설정 (예: 1800초 = 30분)
# 테스트할 때는 20~30초로 낮춰서 확인해 보세요!
SILENCE_TIMEOUT = 30 
# ============================================

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# 봇 상태 기억용 변수
last_message_time = datetime.now()
last_channel = None

@bot.event
async def on_ready():
    print(f'{bot.user} 봇이 로그인했습니다. 반말 컨셉 선톡 루프를 시작합니다.')
    global last_message_time
    last_message_time = datetime.now()
    check_silence.start() # 말이 없는지 감시하는 타이머 시작

@bot.event
async def on_message(message):
    global last_message_time, last_channel
    
    # 봇이 쓴 메시지는 무시
    if message.author == bot.user:
        return

    # 사람이 말을 했으므로 '마지막 대화 시간'과 '채널'을 업데이트
    last_message_time = datetime.now()
    last_channel = message.channel

    # [기능 1: 상대 대화에 자연스럽게 반말로 끼어들기]
    # 언급(@봇), 이름 포함, 혹은 20%의 확률로 대화 흐름에 끼어들기
    should_reply = (
        bot.user.mentioned_in(message) or 
        bot.user.name in message.content or 
        (asyncio.get_event_loop().time() % 5 == 0)
    )

    if should_reply:
        async with message.channel.typing(): # 봇이 타이핑 중인 것처럼 표시
            try:
                # 반말 컨셉을 위한 프롬프트 수정
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
                await message.channel.send(response.text)
            except Exception as e:
                print(f"끼어들기 에러: {e}")

# [기능 2: 서버에 말이 없으면 먼저 반말로 대화 시작하기 (선톡)]
@tasks.loop(seconds=5) # 5초마다 서버가 조용한지 체크
async def check_silence():
    global last_message_time, last_channel
    
    # 마지막 대화로부터 지정된 시간(SILENCE_TIMEOUT)이 지났는지 확인
    if datetime.now() - last_message_time > timedelta(seconds=SILENCE_TIMEOUT):
        # 대화를 보낼 채널 결정 (설정된 ID가 없으면 가장 최근에 대화가 있던 채널)
        target = bot.get_channel(TARGET_CHANNEL_ID) if TARGET_CHANNEL_ID else last_channel
        
        if target:
            try:
                # 대화를 초기화하기 위해 시간 업데이트 (연속 도배 방지)
                last_message_time = datetime.now()
                
                # 반말 선톡을 위한 프롬프트 수정
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
                await target.send(response.text)
            except Exception as e:
                print(f"선톡 에러: {e}")

# 봇 구동
bot.run(DISCORD_TOKEN)
