import os
import discord
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
client_openai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)


@bot.event
async def on_ready():
  print(f"로그인 완료: {bot.user}")


@bot.event
async def on_message(message):
  if message.author == bot.user:
    return

  # 특정 멘션이나 명령어에 반응하도록 설정
  if message.content.startswith("선물님"):
    prompt = message.content[4:].strip()

    response = client_openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
    )

    reply = response.choices[0].message.content
    await message.channel.send(reply)


bot.run(DISCORD_TOKEN)
