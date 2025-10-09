import discord
from discord.ext import commands
from flask import Flask, request, jsonify
from threading import Thread
import os

# Bot setup
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Store email verifications {discord_user_id: email}
email_verifications = {}

# Flask app for webhook
app = Flask(__name__)

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print(f'Bot is in {len(bot.guilds)} servers')

@bot.event
async def on_member_join(member):
    """Send verification DM when someone joins"""
    try:
        embed = discord.Embed(
            title="Welcome! 🎉",
            description="To get your **Subscriber** role, please verify your email.",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="How to verify:",
            value="Reply to this DM with the email you used to purchase the Trading Bot Suite.\n\nExample: `myemail@example.com`",
            inline=False
        )
        await member.send(embed=embed)
    except discord.Forbidden:
        print(f"Could not DM {member.name}")

@bot.event
async def on_message(message):
    """Handle DM verification"""
    # Ignore bot messages
    if message.author.bot:
        return
    
    # Check if it's a DM
    if isinstance(message.channel, discord.DMChannel):
        # Check if message looks like an email
        if '@' in message.content and '.' in message.content:
            email = message.content.strip().lower()
            
            # Store the verification
            email_verifications[message.author.id] = email
            
            await message.channel.send(
                f"✅ Email `{email}` saved!\n\n"
                "Once your purchase is confirmed, you'll automatically receive the **Subscriber** role."
            )
            print(f"Stored email verification: {message.author.name} -> {email}")
        else:
            await message.channel.send(
                "⚠️ That doesn't look like a valid email. Please send your email address.\n"
                "Example: `myemail@example.com`"
            )
    
    await bot.process_commands(message)

@bot.command()
@commands.has_permissions(administrator=True)
async def listverified(ctx):
    """List all verified emails (Admin only)"""
    if not email_verifications:
        await ctx.send("No verified emails yet.")
        return
    
    msg = "**Verified Emails:**\n"
    for user_id, email in email_verifications.items():
        user = await bot.fetch_user(user_id)
        msg += f"• {user.name} - {email}\n"
    
    await ctx.send(msg)

@bot.command()
@commands.has_permissions(administrator=True)
async def manualverify(ctx, member: discord.Member, email: str):
    """Manually verify a user's email (Admin only)"""
    email_verifications[member.id] = email.lower()
    await ctx.send(f"✅ Manually verified {member.name} with email: {email}")

# Webhook endpoint for Zapier
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        email = data.get('email', '').lower()
        action = data.get('action')  # 'add_role' or 'remove_role'
        
        if not email or not action:
            return jsonify({'error': 'Missing email or action'}), 400
        
        # Find user by email
        user_id = None
        for uid, stored_email in email_verifications.items():
            if stored_email == email:
                user_id = uid
                break
        
        if not user_id:
            return jsonify({
                'error': f'No verified user found for email: {email}',
                'note': 'User needs to verify their email in Discord DMs first'
            }), 404
        
        # Process the action asynchronously
        bot.loop.create_task(handle_role_change(user_id, action, email))
        
        return jsonify({
            'success': True,
            'email': email,
            'action': action
        }), 200
        
    except Exception as e:
        print(f"Webhook error: {e}")
        return jsonify({'error': str(e)}), 500

async def handle_role_change(user_id, action, email):
    """Handle adding or removing the Subscriber role"""
    try:
        # Get the first guild (server) the bot is in
        guild = bot.guilds[0]
        
        # Get the member
        member = guild.get_member(user_id)
        if not member:
            print(f"Member not found in server: {user_id}")
            return
        
        # Find or create the Subscriber role
        role = discord.utils.get(guild.roles, name="Subscriber")
        if not role:
            # Create the role if it doesn't exist
            role = await guild.create_role(
                name="Subscriber",
                color=discord.Color.gold(),
                reason="Auto-created for subscription management"
            )
            print(f"Created Subscriber role")
        
        if action == 'add_role':
            await member.add_roles(role)
            
            # Send confirmation DM
            try:
                await member.send(
                    f"🎉 **Subscription Activated!**\n\n"
                    f"Your **Subscriber** role has been assigned.\n"
                    f"You now have access to all premium channels!"
                )
            except discord.Forbidden:
                pass
            
            print(f"✅ Added Subscriber role to {member.name} ({email})")
            
        elif action == 'remove_role':
            await member.remove_roles(role)
            
            # Send goodbye DM
            try:
                await member.send(
                    "Your subscription has been cancelled.\n"
                    "The **Subscriber** role has been removed.\n\n"
                    "Thanks for being a subscriber! Feel free to rejoin anytime."
                )
            except discord.Forbidden:
                pass
            
            # Optionally kick the user (uncomment line below)
            # await member.kick(reason="Subscription cancelled")
            
            print(f"❌ Removed Subscriber role from {member.name} ({email})")
        
    except Exception as e:
        print(f"Error handling role change: {e}")

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'online',
        'bot_name': bot.user.name if bot.user else 'Not connected',
        'verified_users': len(email_verifications)
    }), 200

def run_flask():
    """Run Flask in a separate thread"""
    app.run(host='0.0.0.0', port=8080)

def main():
    """Start both Flask and Discord bot"""
    # Start Flask in background thread
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Start Discord bot
    TOKEN = os.environ.get('DISCORD_TOKEN')
    if not TOKEN:
        print("ERROR: DISCORD_TOKEN not found in environment variables!")
        print("Add DISCORD_TOKEN to Railway environment variables")
        return
    
    print(f"Starting bot on Railway...")
    print(f"Webhook endpoint will be at: https://your-app.railway.app/webhook")
    bot.run(TOKEN)

if __name__ == '__main__':
    main()