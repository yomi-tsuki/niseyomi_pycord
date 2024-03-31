import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
import re
from dotenv import load_dotenv
import os
import pytz  # 追加

# envから読み込み
load_dotenv()

# 環境変数からトークンを取得
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

# スケジューラーのインスタンスを作成
scheduler = AsyncIOScheduler()

# スケジューラーが既に実行中でないかチェック
if not scheduler.running:
    scheduler.start()

# メッセージリンクの正規表現パターン
message_link_pattern = re.compile(r'https://discord\.com/channels/(\d+)/(\d+)/(\d+)')

# 埋め込みメッセージの辞書 {original_message_id: embed_message}
embed_messages = {}

class EventModal(discord.ui.Modal):
    def __init__(
            self, channel_id
            ):
        super().__init__(
            title="スケジュール入力"
            )
        self.channel_id = channel_id

        self.add_item(discord.ui.InputText(
            label="メッセージ（メンション可）", placeholder="MM/DDは固定予定日です @here @everyone"
            ))
        self.add_item(discord.ui.InputText(
            label="活動詳細", placeholder="目的フェーズとか"
            ))
        self.add_item(discord.ui.InputText(
            label="予定時間", placeholder="hh:dd～hh:dd"
            ))
        self.add_item(discord.ui.InputText(
            label="送信日時（※書式厳守※）", placeholder="YYYY-MM-DD HH:MM"
            ))

    async def callback(self, interaction: discord.Interaction):
        event_name = self.children[0].value
        event_details = self.children[1].value
        additional_comments = self.children[2].value
        scheduled_time_str = self.children[3].value

        # 日時とチャンネルIDのバリデーション
        try:
            scheduled_time = datetime.strptime(
                scheduled_time_str, "%Y-%m-%d %H:%M"
                ).replace(tzinfo=pytz.utc).astimezone(pytz.timezone('Asia/Tokyo'))  # 日本時間に変換
        except ValueError:
            await interaction.response.send_message(
                "日時の形式が正しくありません。"
                )
            return

        # 入力された日時が現在の日時よりも過去であるかチェック
        if scheduled_time <= datetime.now(pytz.timezone('Asia/Tokyo')):
            await interaction.response.send_message(
                "指定された日時は現在の日時よりも過去です。"
                )
            return

        # Embedを作成してAuthorの名前とアイコンは適当に指定、活動詳細と予定時間を表示する
        embed = discord.Embed(
            title="固定活動日", description=None, color=discord.Colour.yellow()
            )
        embed.set_author(
            name="絶ちんちろ予定通知", icon_url="https://cdn.discordapp.com/attachments/572675376405544983/1221509604731519006/download20240301004917.png?ex=6612d678&is=66006178&hm=9a4997cf7799c975a74737e2787fbdaebcb739615a3098bb6125f59b6f31c879&"
            )
        embed.add_field(
            name="活動詳細", value=event_details, inline=False
            )
        embed.add_field(
            name="予定時間", value=additional_comments, inline=False
            )
        
        # AllowedMentionsオブジェクトを作成
        allowed_mentions = discord.AllowedMentions(
            everyone=True
            )

        # スケジュールされたメッセージを送信する関数を定義
        async def send_scheduled_message():
            channel = bot.get_channel(self.channel_id)
            if channel:
                await channel.send(f"リマインド: {event_name}", embed=embed, allowed_mentions=allowed_mentions)
            else:
                print(f"チャンネルID {self.channel_id} が見つかりませんでした。")

        # スケジューラーにジョブを追加
        job = scheduler.add_job(send_scheduled_message, 'date', run_date=scheduled_time)

        # レスポンスを送信
        await interaction.response.send_message(
            f"メッセージ: {event_name}\nリマインドは{scheduled_time_str}に<#{self.channel_id}>に送信されます。"
            )

@bot.slash_command(
        name="event", description="スケジュール作成画面を表示します"
        )
async def event(
    ctx: discord.ApplicationContext, channel: discord.Option(discord.TextChannel, "チャンネルを選択してください")
    ):
    modal = EventModal(channel.id)
    await ctx.send_modal(modal)

@bot.event
async def on_ready():
    print(f'{bot.user.name} がオンラインになりました。')

@bot.event
async def on_message(message):
    # メッセージリンクが含まれているかチェック
    match = message_link_pattern.search(message.content)
    if match:
        guild_id, channel_id, message_id = match.groups()
        target_channel = bot.get_channel(int(channel_id))
        try:
            # リンク先のメッセージを取得
            target_message = await target_channel.fetch_message(int(message_id))
            # 埋め込みメッセージを作成
            embed = discord.Embed(description=target_message.content, color=0x00bfff)
            embed.set_author(name=target_message.author.display_name, icon_url=target_message.author.avatar.url)

            # メッセージリンクのフィールドを追加
            embed.add_field(name="メッセージリンク", value=target_message.jump_url, inline=False)
            # チャンネルと日時のフィールドを追加
            channel_time_text = f"チャンネル: #{target_channel.name} | 日時: {target_message.created_at.strftime('%Y-%m-%d %H:%M:%S')}"
            embed.add_field(name="情報", value=channel_time_text, inline=False)

            # ボタンコンポーネントを使ったViewオブジェクトを作成
            view = discord.ui.View(timeout=None)
            view.add_item(discord.ui.Button(label="メッセージ先はこちら", style=discord.ButtonStyle.link, url=target_message.jump_url))  # 修正

            # リンク元に画像がある場合、画像のURLをフィールドとして追加
            if target_message.attachments:
                for index, attachment in enumerate(target_message.attachments, start=1):
                    embed.add_field(name=f'画像 {index}', value=attachment.url, inline=True)

            # 埋め込みメッセージを送信
            embed_message = await message.channel.send(embed=embed, view=view)

            # 辞書に追加
            embed_messages[message.id] = embed_message
        except discord.NotFound:
            await message.channel.send('メッセージが見つかりませんでした。')
            await bot.process_commands(message)

@bot.event
async def on_message_delete(deleted_message):
    # 削除されたメッセージが埋め込みメッセージの元であるかチェック
    if deleted_message.id in embed_messages:
        # 対応する埋め込みメッセージを削除
        await embed_messages[deleted_message.id].delete()
        # 辞書から削除
        del embed_messages[deleted_message.id]

# トークンを使用してBOTを起動
bot.run(TOKEN)
