import discord
from discord.ext import commands
from discord.commands import Option
from discord import Option, slash_command
from discord.utils import escape_markdown, get
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta
import re, asyncio
from dotenv import load_dotenv
import os
from pytz import timezone

# 日本時間のタイムゾーンを取得
jst_tz = timezone('Asia/Tokyo')

# envから読み込み
load_dotenv()

# 環境変数からトークンを取得
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True

# botのインスタンスを作成
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
    def __init__(self, channel_id):
        super().__init__(title="スケジュール入力")
        self.channel_id = channel_id

        self.add_item(discord.ui.InputText(label="メッセージ（メンション可）", placeholder="MM/DDは固定予定日です @here @everyone"))
        self.add_item(discord.ui.InputText(label="活動詳細", placeholder="目的フェーズとか"))
        self.add_item(discord.ui.InputText(label="予定時間", placeholder="hh:dd～hh:dd"))
        self.add_item(discord.ui.InputText(label="送信日時（※書式厳守※）", placeholder="YYYY-MM-DD HH:MM"))

    async def callback(self, interaction: discord.Interaction):
        event_name = self.children[0].value
        event_details = self.children[1].value
        additional_comments = self.children[2].value
        scheduled_time_str = self.children[3].value

        # 日時とチャンネルIDのバリデーション
        try:
            # ユーザーが入力した日時を日本時間として扱う
            scheduled_time = datetime.strptime(scheduled_time_str, "%Y-%m-%d %H:%M")
            scheduled_time = scheduled_time.astimezone(jst_tz)
            
            # スケジュールされた時間を12時間早める
            scheduled_time -= timedelta(hours=12)
        except ValueError:
            await interaction.response.send_message("日時の形式が正しくありません。")
            return

        # Embedを作成してAuthorの名前とアイコンは適当に指定、活動詳細と予定時間を表示する
        embed = discord.Embed(title="固定活動日", description=None, color=discord.Colour.yellow())
        embed.set_author(name="予定通知", icon_url="https://cdn.discordapp.com/attachments/572675376405544983/1221509604731519006/download20240301004917.png")
        embed.add_field(name="活動詳細", value=event_details, inline=False)
        embed.add_field(name="予定時間", value=additional_comments, inline=False)

        # AllowedMentionsオブジェクトを作成
        allowed_mentions = discord.AllowedMentions(everyone=True)

        # スケジュールされたメッセージを送信する関数を定義
        async def send_scheduled_message():
            channel = bot.get_channel(self.channel_id)
            if channel:
                await channel.send(f"リマインド: {event_name}", embed=embed, allowed_mentions=allowed_mentions)
            else:
                print(f"チャンネルID {self.channel_id} が見つかりませんでした。")

        # スケジューラーにジョブを追加
        scheduler.add_job(send_scheduled_message, 'date', run_date=scheduled_time)

        # レスポンスを送信
        await interaction.response.send_message(f"メッセージ: {event_name}\nリマインドは{scheduled_time.strftime('%Y-%m-%d %H:%M')}に<#{self.channel_id}>に送信されます。")

@bot.slash_command(name="event", description="スケジュール作成画面を表示します")
async def event(ctx: discord.ApplicationContext, channel: discord.Option(discord.TextChannel, "チャンネルを選択してください")): # type: ignore
    modal = EventModal(channel.id)
    await ctx.send_modal(modal)

@bot.event
async def on_message(message):
    # メッセージにtwitter.comまたはx.comのURLが含まれているか確認
    if contains_url(message, 'https://twitter.com/') or contains_url(message, 'https://x.com/'):
        # Embedを削除しようと試みる
        try:
            await message.edit(suppress=True)
            print(f"Embed removed from message: {message.content}")
        except discord.errors.Forbidden:
            print("Bot does not have permission to edit message.")
        except discord.errors.NotFound:
            print("Message not found.")
        except discord.errors.HTTPException as e:
            print(f"Failed to remove embed: {e}")

        # URLを変換して返信する
        converted_message = convert_url(message)
        # メンションせずにメッセージを送信する
        reply_message = await message.reply(converted_message, mention_author=False)
        # 返信のIDを記録する
        message_reply_map[message.id] = reply_message.id
        print(f"Replied with converted URL without mentioning the author: {converted_message}")

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
            view.add_item(discord.ui.Button(label="メッセージ先はこちら", style=discord.ButtonStyle.link, url=target_message.jump_url))

            # リンク元に画像がある場合、画像のURLをフィールドとして追加
            if target_message.attachments:
                for index, attachment in enumerate(target_message.attachments, start=1):
                    embed.add_field(name=f'画像 {index}', value=attachment.url, inline=True)

            # 埋め込みメッセージを送信
            embed_message = await message.channel.send(embed=embed, view=view)

            # 辞書に追加
            embed_messages[message.id] = embed_message
        except discord.NotFound:
            await message.channel.send('メッセージが見つかりませんでした.')
    else:
        # 他のコマンドを処理
        await bot.process_commands(message)

# URLを検出する関数
def contains_url(message, domain):
    return any(domain in word for word in message.content.split())

# URLを変換する関数
def convert_url(message):
    words = message.content.split()
    new_words = []
    for word in words:
        # 'https://vxtwitter.com'が既に含まれている場合は変換しない
        if 'https://twitter.com/' in word and 'https://vxtwitter.com/' not in word:
            new_word = word.replace('https://twitter.com/', 'https://vxtwitter.com/')
            new_words.append(new_word)
        elif 'https://x.com/' in word and 'https://vxtwitter.com/' not in word:
            new_word = word.replace('https://x.com/', 'https://vxtwitter.com/')
            new_words.append(new_word)
        else:
            new_words.append(word)
    return ' '.join(new_words)

# グローバル変数としてmessage_reply_mapを定義
message_reply_map = {}

# メッセージ削除イベントリスナー
@bot.event
async def on_message_delete(deleted_message):
    # 削除されたメッセージが埋め込みメッセージの元であるかチェック
    if deleted_message.id in embed_messages:
        # 対応する埋め込みメッセージを削除
        await embed_messages[deleted_message.id].delete()
        # 辞書から削除
        del embed_messages[deleted_message.id]

    global message_reply_map
    # 削除されたメッセージに対するBotの返信があるか確認
    if deleted_message.id in message_reply_map:
        # Botの返信を取得
        reply_message_id = message_reply_map[deleted_message.id]
        # Botの返信を削除する
        try:
            reply_message = await deleted_message.channel.fetch_message(reply_message_id)
            await reply_message.delete()
            print(f"Bot's reply message deleted: {reply_message.content}")
        except discord.errors.NotFound:
            print("Bot's reply message not found.")
        except discord.errors.HTTPException as e:
            print(f"Failed to delete Bot's reply message: {e}")
        # 辞書からエントリを削除
        del message_reply_map[deleted_message.id]

# 辞書のキーを大文字小文字を区別する形で登録
websites = {
    "FF14Lodestone": "https://jp.finalfantasyxiv.com/lodestone/",
    "Eorzean": "https://www.eorzean.info/"
}

@bot.slash_command(name="website", description="指定されたWebサイトのURLを返信します")
async def website(ctx, site: Option(str, "選択するWebサイト", choices=list(websites.keys()))): # type: ignore
    # キーが辞書に存在するか確認する
    if site in websites:
        await ctx.respond(f"選択されたWebサイトのURLです。\n##  {site}: {websites[site]}")
    else:
        # キーが存在しない場合はエラーメッセージを返す
        await ctx.respond(f"選択されたWebサイト '{site}' はKeyに登録されていません。")

# ユーザー名に含まれるMarkdown特殊文字をエスケープする関数
def escape_markdown(text):
    # Markdownの特殊文字をエスケープする
    escape_characters = ['*', '_', '`', '~']
    for char in escape_characters:
        text = text.replace(char, f'\\{char}')
    return text

# 下記はサーバーに所属していればユーザー選択可。脱退者についてはユーザーIDでの指定必須。
# 指定ユーザーのメッセージ一括削除。指定チャンネル。
@bot.slash_command(name="delete_messages", description="特定のチャンネルのユーザーのメッセージを削除します")
async def delete_messages(ctx, user: Option(discord.User, "ユーザーを選択/ユーザーIDを指定"), channel: Option(discord.TextChannel, "チャンネルを選択")): # type: ignore
    if ctx.author.guild_permissions.administrator:
        def is_user(m):
            return m.author == user

        deleted = await channel.purge(check=is_user)
        await ctx.respond(f'{channel.mention}から{escape_markdown(user.name)}のメッセージを{len(deleted)}件削除しました。', ephemeral=True)
    else:
        await ctx.respond("この機能は管理者権限が必要です。", ephemeral=True)

# 指定ユーザーのメッセージ一括削除。全チャンネル。
@bot.slash_command(name="delete_messages_all", description="全てのチャンネルのユーザーのメッセージを削除します")
async def delete_messages_all(ctx, user: Option(discord.User, "ユーザーを選択/ユーザーIDを指定")): # type: ignore
    if ctx.author.guild_permissions.administrator:
        def is_user(m):
            return m.author == user
        await ctx.defer(ephemeral=True)
        deleted_messages = 0
        for channel in ctx.guild.text_channels:
            deleted = await channel.purge(check=is_user)
            deleted_messages += len(deleted)
        await ctx.followup.send(f'全てのチャンネルから{escape_markdown(user.name)}のメッセージを{deleted_messages}件削除しました。')
    else:
        await ctx.respond("この機能は管理者権限が必要です。", ephemeral=True)

@bot.event
async def on_ready():
    # 現在の日本時間を取得
    jst_time = datetime.now(jst_tz)
    print(f'{bot.user.name} が 日本時間: {jst_time.strftime("%Y-%m-%d %H:%M:%S")} にオンラインになりました。')

# トークンを使用してBOTを起動
bot.run(TOKEN)
