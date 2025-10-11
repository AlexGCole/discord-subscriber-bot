import discord
from discord.ext import commands
from flask import Flask, request, jsonify
from threading import Thread
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json

# Bot setup
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Flask app for webhook
app = Flask(__name__)

# ============================================
# PRODUCT → ROLE MAPPING
# ============================================
PRODUCT_ROLE_MAP = {
    '7995703263412': ['Bot Suite', 'Member'],
    '7995706015924': ['Bot Suite', 'Member'],
    '7996025995444' : ['Indicator Suite', 'Member']
}

# Google Sheets setup
def get_sheets_client():
    """Connect to Google Sheets"""
    try:
        # Get credentials from environment variable
        creds_json = os.environ.get('GOOGLE_SHEETS_CREDS')
        if not creds_json:
            print("ERROR: GOOGLE_SHEETS_CREDS not found!")
            return None
        
        creds_dict = json.loads(creds_json)
        scope = ['https://spreadsheets.google.com/feeds',
                 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        print(f"Error connecting to Google Sheets: {e}")
        return None

def get_worksheet():
    """Get the subscriber tracking worksheet"""
    try:
        client = get_sheets_client()
        if not client:
            return None
        
        sheet_name = os.environ.get('GOOGLE_SHEET_NAME', 'Market Sniper Subscriptions')
        spreadsheet = client.open(sheet_name)
        worksheet = spreadsheet.sheet1
        return worksheet
    except Exception as e:
        print(f"Error getting worksheet: {e}")
        return None

def find_user_in_sheets(email):
    """Find user in Google Sheets by email"""
    try:
        worksheet = get_worksheet()
        if not worksheet:
            return None
        
        # Get all records
        records = worksheet.get_all_records()
        
        # Search for email
        email = email.lower().strip()
        for row_num, record in enumerate(records, start=2):  # start=2 because row 1 is header
            sheet_email = str(record.get('Email', '')).lower().strip()
            if sheet_email == email:
                return {
                    'row': row_num,
                    'data': record
                }
        return None
    except Exception as e:
        print(f"Error finding user in sheets: {e}")
        return None

def update_discord_verified_status(email, discord_username, discord_user_id, verified=True):
    """Update Discord verification status in Google Sheets"""
    try:
        worksheet = get_worksheet()
        if not worksheet:
            return False
        
        user_data = find_user_in_sheets(email)
        if not user_data:
            return False
        
        row_num = user_data['row']
        
        # Find column numbers for Discord fields
        headers = worksheet.row_values(1)
        discord_verified_col = None
        discord_username_col = None
        discord_user_id_col = None
        
        for i, header in enumerate(headers, start=1):
            if 'Discord Verified' in header:
                discord_verified_col = i
            if 'Discord Username' in header and 'ID' not in header:
                discord_username_col = i
            if 'Discord User ID' in header:
                discord_user_id_col = i
        
        # Update cells
        if discord_verified_col:
            worksheet.update_cell(row_num, discord_verified_col, 'Yes' if verified else 'No')
        if discord_username_col:
            worksheet.update_cell(row_num, discord_username_col, discord_username)
        if discord_user_id_col:
            worksheet.update_cell(row_num, discord_user_id_col, str(discord_user_id))
        
        print(f"Updated sheets for {email}: verified={verified}, username={discord_username}, user_id={discord_user_id}")
        return True
    except Exception as e:
        print(f"Error updating sheets: {e}")
        return False

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print(f'Bot is in {len(bot.guilds)} servers')
    print(f'Webhook endpoint ready at: /webhook')
    
    # Test Google Sheets connection
    worksheet = get_worksheet()
    if worksheet:
        print(f"✅ Connected to Google Sheets: {worksheet.spreadsheet.title}")
    else:
        print("⚠️ Could not connect to Google Sheets - check credentials")

@bot.event
async def on_member_join(member):
    """Send verification DM when someone joins"""
    try:
        embed = discord.Embed(
            title="Welcome to Market Sniper! 🎉",
            description="To get your role and access, please verify your email.",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="How to verify:",
            value="Reply to this DM with the email you used to purchase the Trading Bot Suite.\n\nExample: `myemail@example.com`",
            inline=False
        )
        embed.add_field(
            name="Already purchased?",
            value="Once you verify your email, your role will be assigned automatically based on your subscription.",
            inline=False
        )
        await member.send(embed=embed)
    except discord.Forbidden:
        print(f"Could not DM {member.name}")

@bot.event
async def on_message(message):
    """Handle DM verification"""
    if message.author.bot:
        return
    
    # Check if it's a DM
    if isinstance(message.channel, discord.DMChannel):
        # Check if message looks like an email
        if '@' in message.content and '.' in message.content:
            email = message.content.strip().lower()
            
            # Check if email exists in Google Sheets
            user_data = find_user_in_sheets(email)
            
            if user_data:
                # Email found in sheets!
                # Check both 'Status' and 'Payment Status' columns
                status = user_data['data'].get('Status') or user_data['data'].get('Payment Status', 'Unknown')
                
                # Only accept PAID as valid status
                if status.upper() == 'PAID':
                    # CHECK IF EMAIL IS ALREADY CLAIMED BY ANOTHER USER
                    existing_discord_verified = user_data['data'].get('Discord Verified', '').lower()
                    existing_discord_user_id = str(user_data['data'].get('Discord User ID', '')).strip()
                    current_user_id = str(message.author.id)
                    
                    # If already verified and it's a different user, BLOCK
                    if existing_discord_verified == 'yes' and existing_discord_user_id and existing_discord_user_id != current_user_id:
                        existing_username = user_data['data'].get('Discord Username', 'another user')
                        await message.channel.send(
                            f"🚫 **Email Already Registered**\n\n"
                            f"The email `{email}` is already linked to another Discord account (`{existing_username}`).\n\n"
                            f"If this is your email and you need to update your Discord account, please contact support."
                        )
                        print(f"⚠️ Blocked hijack attempt: {message.author.name} (ID: {current_user_id}) tried to use {email} (already owned by user ID {existing_discord_user_id})")
                        return
                    
                    # If it's the same user re-verifying, allow it
                    if existing_discord_verified == 'yes' and existing_discord_user_id == current_user_id:
                        await message.channel.send(
                            f"ℹ️ You've already verified this email!\n\n"
                            f"Your account is already linked and you have your role. "
                            f"If you're missing your role, please contact support."
                        )
                        return
                    
                    # New verification - store both username and user ID
                    discord_username = f"{message.author.name}"
                    discord_user_id = str(message.author.id)
                    update_discord_verified_status(email, discord_username, discord_user_id, True)
                    
                    await message.channel.send(
                        f"✅ Email `{email}` verified!\n\n"
                        f"Your payment is confirmed (**{status}**). "
                        f"Assigning your role now..."
                    )
                    
                    # Try to assign role immediately
                    guild = bot.guilds[0] if bot.guilds else None
                    if guild:
                        member = guild.get_member(message.author.id)
                        if member:
                            bot.loop.create_task(assign_subscriber_role(member, email))
                    
                    print(f"✅ Email verified: {message.author.name} (ID: {discord_user_id}) -> {email}")
                else:
                    await message.channel.send(
                        f"⚠️ Email `{email}` found, but payment status is: **{status}**\n\n"
                        f"Access is only granted for **PAID** subscriptions. "
                        f"Please complete your purchase or contact support if this is an error."
                    )
            else:
                # Email NOT found in sheets
                await message.channel.send(
                    f"❌ Email `{email}` not found in our system.\n\n"
                    f"Please make sure:\n"
                    f"• You've completed your purchase\n"
                    f"• You're using the exact email from your Shopify order\n"
                    f"• Your order has been processed (may take a few minutes)\n\n"
                    f"If you just purchased, wait 2-3 minutes and try again."
                )
                print(f"❌ Email not found in sheets: {email}")
    
    await bot.process_commands(message)

async def assign_subscriber_role(member, email):
    """Assign the appropriate role based on product purchased"""
    try:
        guild = member.guild
        
        # Get user's product from sheets
        user_data = find_user_in_sheets(email)
        if not user_data:
            print(f"⚠️ Could not find user data for {email}")
            return
        
        # Use "Product ID" (with space and capitals) as it appears in your sheet
        product_id = str(user_data['data'].get('Product ID', '')).strip()
        
        # Get the role name based on product
        role_name = PRODUCT_ROLE_MAP.get(product_id)
        
        if not role_name:
            print(f"⚠️ No role mapping found for product ID: {product_id}")
            # Fallback to default Subscriber role
            role_name = "Subscriber"
        
        # Get or create the role
        role = discord.utils.get(guild.roles, name=role_name)
        
        if not role:
            # If role doesn't exist, create it
            role = await guild.create_role(
                name=role_name,
                color=discord.Color.blue(),
                reason="Auto-created for subscription management"
            )
            print(f"Created new role: {role_name}")
        
        # Add role
        await member.add_roles(role)
        
        # Send confirmation DM
        try:
            await member.send(
                f"🎉 **Subscription Activated!**\n\n"
                f"Your **{role.name}** role has been assigned.\n"
                f"You now have access to all premium channels!"
            )
        except discord.Forbidden:
            pass
        
        print(f"✅ Added {role.name} role to {member.name} ({email}) for product {product_id}")
        
    except Exception as e:
        print(f"Error assigning role: {e}")

@bot.command()
@commands.has_permissions(administrator=True)
async def checksheet(ctx, email: str):
    """Check if email exists in Google Sheets (Admin only)"""
    user_data = find_user_in_sheets(email)
    if user_data:
        data = user_data['data']
        msg = f"**Found in Sheet (Row {user_data['row']}):**\n"
        for key, value in data.items():
            msg += f"• {key}: {value}\n"
        await ctx.send(msg)
    else:
        await ctx.send(f"❌ Email `{email}` not found in Google Sheets")

@bot.command()
@commands.has_permissions(administrator=True)
async def syncsheets(ctx):
    """Sync all Active subscribers from Sheets (Admin only)"""
    try:
        worksheet = get_worksheet()
        if not worksheet:
            await ctx.send("❌ Could not connect to Google Sheets")
            return
        
        records = worksheet.get_all_records()
        synced = 0
        
        for record in records:
            payment_status = record.get('Payment Status') or record.get('Status', '')
            discord_verified = record.get('Discord Verified', '').lower()
            
            if payment_status.upper() == 'PAID' and discord_verified == 'yes':
                synced += 1
        
        await ctx.send(f"✅ Checked {len(records)} records, {synced} paid & verified subscribers")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

# Webhook endpoint for Zapier
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        email = data.get('email', '').lower()
        action = data.get('action')
        
        print(f"Webhook received: email={email}, action={action}")
        
        if not email or not action:
            return jsonify({'error': 'Missing email or action'}), 400
        
        # Validate action
        valid_actions = ['add_role', 'remove_role', 'kick']
        if action not in valid_actions:
            return jsonify({'error': f'Invalid action. Must be one of: {valid_actions}'}), 400
        
        # Check if user exists in Google Sheets
        user_data = find_user_in_sheets(email)
        
        if not user_data:
            return jsonify({
                'error': f'Email {email} not found in Google Sheets',
                'note': 'Make sure Zapier added the user to sheets first'
            }), 404
        
        # Check payment status
        payment_status = user_data['data'].get('Payment Status') or user_data['data'].get('Status', 'Unknown')
        payment_status_upper = payment_status.upper()
        
        # For add_role: Must be PAID
        # For remove_role/kick: Must be Refunded or Cancelled (we're removing access)
        if action == 'add_role':
            if payment_status_upper != 'PAID':
                return jsonify({
                    'error': f'Payment status is {payment_status}, must be PAID to add role',
                    'note': 'Only PAID subscriptions get Discord access'
                }), 400
        elif action in ['remove_role', 'kick']:
            # Only process removals if status is Refunded or Cancelled
            if payment_status_upper not in ['REFUNDED', 'CANCELLED']:
                return jsonify({
                    'error': f'Payment status is {payment_status}, must be REFUNDED or CANCELLED to remove access',
                    'note': 'Only refunded or cancelled orders trigger role removal'
                }), 400
            print(f"Processing {action} for {email} with status: {payment_status}")
        
        # Check if user verified their Discord
        discord_verified = user_data['data'].get('Discord Verified', '').lower()
        
        if discord_verified != 'yes':
            return jsonify({
                'error': f'User {email} has not verified their Discord account yet',
                'note': 'User needs to DM the bot with their email first'
            }), 400
        
        # Get Discord User ID from sheets (for finding the member)
        discord_user_id = user_data['data'].get('Discord User ID', '')
        
        if not discord_user_id:
            return jsonify({
                'error': f'No Discord User ID stored for {email}',
                'note': 'User needs to verify via DM first'
            }), 400
        
        # Find the Discord user and process action
        bot.loop.create_task(handle_role_change_by_user_id(discord_user_id, action, email))
        
        return jsonify({
            'success': True,
            'email': email,
            'discord_user_id': discord_user_id,
            'action': action,
            'payment_status': payment_status
        }), 200
        
    except Exception as e:
        print(f"Webhook error: {e}")
        return jsonify({'error': str(e)}), 500

async def handle_role_change_by_user_id(discord_user_id, action, email):
    """Handle role change using Discord User ID from sheets"""
    try:
        if not bot.guilds:
            print("Bot not in any servers")
            return
        
        guild = bot.guilds[0]
        
        # Get member by user ID
        try:
            user_id = int(discord_user_id)
            member = guild.get_member(user_id)
        except (ValueError, TypeError):
            print(f"Invalid user ID format: {discord_user_id}")
            return
        
        if not member:
            print(f"Member not found in server: {discord_user_id}")
            return
        
        # Get user's product from sheets to determine which role to assign/remove
        user_data = find_user_in_sheets(email)
        product_id = str(user_data['data'].get('Product ID', '')).strip() if user_data else None
        role_name = PRODUCT_ROLE_MAP.get(product_id) if product_id else None
        
        # Get the appropriate role
        if role_name:
            role = discord.utils.get(guild.roles, name=role_name)
        else:
            # Fallback to Subscriber role
            role = discord.utils.get(guild.roles, name="Subscriber")
        
        if not role:
            role = await guild.create_role(
                name=role_name or "Subscriber",
                color=discord.Color.blue(),
                reason="Auto-created for subscription management"
            )
        
        if action == 'add_role':
            await member.add_roles(role)
            
            try:
                await member.send(
                    f"🎉 **Subscription Activated!**\n\n"
                    f"Your **{role.name}** role has been assigned.\n"
                    f"You now have access to all premium channels!"
                )
            except discord.Forbidden:
                pass
            
            print(f"✅ Added {role.name} role to {member.name} ({email})")
            
        elif action == 'remove_role':
            await member.remove_roles(role)
            
            # Get username for update
            discord_username = user_data['data'].get('Discord Username', '') if user_data else ''
            update_discord_verified_status(email, discord_username, discord_user_id, False)
            
            try:
                await member.send(
                    f"Your subscription has been cancelled.\n"
                    f"The **{role.name}** role has been removed.\n\n"
                    f"You can still hang out in the server! "
                    f"Rejoin anytime by resubscribing. 😊"
                )
            except discord.Forbidden:
                pass
            
            print(f"❌ Removed {role.name} role from {member.name} ({email})")
            
        elif action == 'kick':
            await member.remove_roles(role)
            
            # Get username for update
            discord_username = user_data['data'].get('Discord Username', '') if user_data else ''
            update_discord_verified_status(email, discord_username, discord_user_id, False)
            
            try:
                await member.send(
                    "Your subscription has been cancelled.\n\n"
                    "You've been removed from the server. "
                    "Thanks for being a subscriber! Feel free to rejoin anytime. 👋"
                )
            except discord.Forbidden:
                pass
            
            import asyncio
            await asyncio.sleep(1)
            
            await member.kick(reason=f"Subscription cancelled for {email}")
            
            print(f"🚪 Kicked {member.name} ({email}) from server")
        
    except Exception as e:
        print(f"Error handling role change: {e}")

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    worksheet = get_worksheet()
    sheets_connected = worksheet is not None
    
    return jsonify({
        'status': 'online',
        'bot_name': bot.user.name if bot.user else 'Not connected',
        'sheets_connected': sheets_connected
    }), 200

def run_flask():
    """Run Flask in a separate thread"""
    app.run(host='0.0.0.0', port=8080)

def main():
    """Start both Flask and Discord bot"""
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    TOKEN = os.environ.get('DISCORD_TOKEN')
    if not TOKEN:
        print("ERROR: DISCORD_TOKEN not found in environment variables!")
        return
    
    print(f"Starting bot with Google Sheets integration...")
    bot.run(TOKEN)

if __name__ == '__main__':
    main()