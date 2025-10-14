import discord
from discord.ext import commands
from flask import Flask, request, jsonify
from threading import Thread
import os
import gspread
from google.oauth2.service_account import Credentials
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
    '7995703263412': ['Bot Suite', 'Member'],  # Monthly
    '7995706015924': ['Bot Suite', 'Member'],  # Annual
    '7996025995444': ['Indicator Suite', 'Member'],
    '7995945418932': ['Setup']  # Setup fee - tracking only, NO server access
}

# Products that grant server access
ACCESS_PRODUCTS = ['7995703263412', '7995706015924', '7996025995444']

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
        
        # Use modern google-auth library
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
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

def find_all_user_rows(email):
    """Find ALL rows for a user by email (handles multiple products)"""
    try:
        worksheet = get_worksheet()
        if not worksheet:
            return []
        
        # Get all records - use empty2zero to handle empty cells
        try:
            records = worksheet.get_all_records(empty2zero=False, head=1, default_blank='')
        except Exception as e:
            print(f"Error with get_all_records: {e}")
            # Fallback: get all values and parse manually
            all_values = worksheet.get_all_values()
            if len(all_values) < 2:
                print("No data rows found in sheet")
                return []
            
            headers = all_values[0]
            records = []
            for row in all_values[1:]:
                record = {}
                for i, header in enumerate(headers):
                    if i < len(row):
                        record[header] = row[i]
                    else:
                        record[header] = ''
                records.append(record)
        
        # Search for all matching emails
        email = email.lower().strip()
        matching_rows = []
        
        for row_num, record in enumerate(records, start=2):
            sheet_email = str(record.get('Email', '')).lower().strip()
            if sheet_email == email:
                matching_rows.append({
                    'row': row_num,
                    'data': record
                })
        
        print(f"Found {len(matching_rows)} row(s) for email: {email}")
        return matching_rows
    except Exception as e:
        print(f"Error finding user in sheets: {e}")
        import traceback
        traceback.print_exc()
        return []

def find_user_in_sheets(email):
    """Find user in Google Sheets by email (returns first match for backwards compatibility)"""
    rows = find_all_user_rows(email)
    return rows[0] if rows else None

def update_discord_verified_status_all_rows(email, discord_username, discord_user_id, verified=True):
    """Update Discord verification status for ALL rows with this email"""
    try:
        worksheet = get_worksheet()
        if not worksheet:
            return False
        
        user_rows = find_all_user_rows(email)
        if not user_rows:
            return False
        
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
        
        # Update ALL rows with this email
        for user_row in user_rows:
            row_num = user_row['row']
            
            if discord_verified_col:
                worksheet.update_cell(row_num, discord_verified_col, 'Yes' if verified else 'No')
            if discord_username_col:
                worksheet.update_cell(row_num, discord_username_col, discord_username)
            if discord_user_id_col:
                worksheet.update_cell(row_num, discord_user_id_col, str(discord_user_id))
            
            product_id = user_row['data'].get('Product ID', 'Unknown')
            print(f"Updated row {row_num} (Product {product_id}) for {email}: verified={verified}")
        
        return True
    except Exception as e:
        print(f"Error updating sheets: {e}")
        return False

def update_discord_verified_status(email, discord_username, discord_user_id, verified=True):
    """Wrapper for backwards compatibility - now updates ALL rows"""
    return update_discord_verified_status_all_rows(email, discord_username, discord_user_id, verified)

def has_active_subscription(email):
    """Check if user has an active subscription (not just setup)"""
    user_rows = find_all_user_rows(email)
    
    for row in user_rows:
        product_id = str(row['data'].get('Product ID', '')).strip()
        status = row['data'].get('Status') or row['data'].get('Payment Status', 'Unknown')
        
        # Check if this is an access-granting product and it's paid
        if product_id in ACCESS_PRODUCTS and status.upper() == 'PAID':
            return True
    
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
            value="Reply to this DM with the email you used to purchase.\n\nExample: `myemail@example.com`",
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
            
            # Get ALL rows for this email
            user_rows = find_all_user_rows(email)
            
            if user_rows:
                # Check if ANY row has a different Discord user already verified
                for row in user_rows:
                    existing_discord_verified = row['data'].get('Discord Verified', '').lower()
                    existing_discord_user_id = str(row['data'].get('Discord User ID', '')).strip()
                    current_user_id = str(message.author.id)
                    
                    # If already verified and it's a different user, BLOCK
                    if existing_discord_verified == 'yes' and existing_discord_user_id and existing_discord_user_id != current_user_id:
                        existing_username = row['data'].get('Discord Username', 'another user')
                        await message.channel.send(
                            f"🚫 **Email Already Registered**\n\n"
                            f"The email `{email}` is already linked to another Discord account (`{existing_username}`).\n\n"
                            f"If this is your email and you need to update your Discord account, please contact support."
                        )
                        print(f"⚠️ Blocked hijack attempt: {message.author.name} (ID: {current_user_id}) tried to use {email} (already owned by user ID {existing_discord_user_id})")
                        return
                
                # Check if user has an active subscription (not just setup)
                has_access = has_active_subscription(email)
                
                if not has_access:
                    # User only has setup product or no PAID products
                    await message.channel.send(
                        f"⚠️ **Setup Product Only**\n\n"
                        f"The email `{email}` is registered, but you only have the setup fee product.\n\n"
                        f"To get Discord access, you need to purchase a monthly or annual subscription. "
                        f"The setup fee alone does not grant server access."
                    )
                    print(f"⚠️ User {email} tried to verify but only has setup product")
                    return
                
                # If it's the same user re-verifying, allow it
                if user_rows[0]['data'].get('Discord Verified', '').lower() == 'yes' and \
                   str(user_rows[0]['data'].get('Discord User ID', '')).strip() == str(message.author.id):
                    await message.channel.send(
                        f"ℹ️ You've already verified this email!\n\n"
                        f"Your account is already linked and you have your role. "
                        f"If you're missing your role, please contact support."
                    )
                    return
                
                # New verification - update ALL rows for this email
                discord_username = f"{message.author.name}"
                discord_user_id = str(message.author.id)
                update_discord_verified_status_all_rows(email, discord_username, discord_user_id, True)
                
                await message.channel.send(
                    f"✅ Email `{email}` verified!\n\n"
                    f"Your subscription is confirmed. Assigning your roles now..."
                )
                
                # Try to assign role immediately
                guild = bot.guilds[0] if bot.guilds else None
                if guild:
                    member = guild.get_member(message.author.id)
                    if member:
                        bot.loop.create_task(assign_all_subscriber_roles(member, email))
                
                print(f"✅ Email verified: {message.author.name} (ID: {discord_user_id}) -> {email} (updated {len(user_rows)} rows)")
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

async def assign_all_subscriber_roles(member, email):
    """Assign roles for ALL products the user has purchased"""
    try:
        guild = member.guild
        
        # Get ALL user's products from sheets
        user_rows = find_all_user_rows(email)
        if not user_rows:
            print(f"⚠️ Could not find user data for {email}")
            return
        
        all_roles_to_assign = set()  # Use set to avoid duplicates
        
        # Collect roles from all PAID products
        for row in user_rows:
            status = row['data'].get('Status') or row['data'].get('Payment Status', 'Unknown')
            
            if status.upper() == 'PAID':
                product_id = str(row['data'].get('Product ID', '')).strip()
                role_names = PRODUCT_ROLE_MAP.get(product_id)
                
                if role_names:
                    if isinstance(role_names, str):
                        all_roles_to_assign.add(role_names)
                    else:
                        all_roles_to_assign.update(role_names)
        
        if not all_roles_to_assign:
            print(f"⚠️ No valid roles found for {email}")
            return
        
        assigned_roles = []
        
        # Assign each unique role
        for role_name in all_roles_to_assign:
            role = discord.utils.get(guild.roles, name=role_name)
            
            if not role:
                # Create role if it doesn't exist
                role = await guild.create_role(
                    name=role_name,
                    color=discord.Color.blue(),
                    reason="Auto-created for subscription management"
                )
                print(f"Created new role: {role_name}")
            
            await member.add_roles(role)
            assigned_roles.append(role.name)
        
        # Send confirmation DM
        try:
            roles_text = ", ".join([f"**{r}**" for r in assigned_roles])
            await member.send(
                f"🎉 **Subscription Activated!**\n\n"
                f"Your roles have been assigned: {roles_text}\n"
                f"You now have access to all premium channels!"
            )
        except discord.Forbidden:
            pass
        
        print(f"✅ Added roles {assigned_roles} to {member.name} ({email})")
        
    except Exception as e:
        print(f"Error assigning roles: {e}")

async def assign_subscriber_role(member, email):
    """Backwards compatibility wrapper"""
    await assign_all_subscriber_roles(member, email)
        
@bot.command()
@commands.has_permissions(administrator=True)
async def checksheet(ctx, email: str):
    """Check if email exists in Google Sheets (Admin only)"""
    user_rows = find_all_user_rows(email)
    if user_rows:
        msg = f"**Found {len(user_rows)} row(s) for {email}:**\n\n"
        for i, user_row in enumerate(user_rows, 1):
            data = user_row['data']
            msg += f"**Row {user_row['row']}:**\n"
            for key, value in data.items():
                msg += f"• {key}: {value}\n"
            msg += "\n"
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
        product_id = data.get('product_id', '').strip()  # NEW: Get specific product ID
        
        print(f"Webhook received: email={email}, action={action}, product_id={product_id}")
        
        if not email or not action:
            return jsonify({'error': 'Missing email or action'}), 400
        
        # Validate action
        valid_actions = ['add_role', 'remove_role', 'kick']
        if action not in valid_actions:
            return jsonify({'error': f'Invalid action. Must be one of: {valid_actions}'}), 400
        
        # Check if user exists in Google Sheets
        user_rows = find_all_user_rows(email)
        
        if not user_rows:
            return jsonify({
                'error': f'Email {email} not found in Google Sheets',
                'note': 'Make sure Zapier added the user to sheets first'
            }), 404
        
        # For add_role: Check if user is verified in ANY of their ACCESS PRODUCT rows
        # Don't check setup products for verification - only actual subscription products
        if action == 'add_role':
            # Find Discord User ID from any ACCESS PRODUCT row that's verified
            discord_user_id = None
            for row in user_rows:
                row_product_id = str(row['data'].get('Product ID', '')).strip()
                # Only check verification on products that grant access (not setup)
                if row_product_id in ACCESS_PRODUCTS:
                    if row['data'].get('Discord Verified', '').lower() == 'yes':
                        discord_user_id = row['data'].get('Discord User ID', '')
                        if discord_user_id:
                            break
            
            if not discord_user_id:
                return jsonify({
                    'error': f'User {email} has not verified their Discord account yet',
                    'note': 'User needs to DM the bot with their email first or already be in the server'
                }), 400
            
            # Check if this is a setup product (no server access)
            if product_id and product_id not in ACCESS_PRODUCTS:
                return jsonify({
                    'success': True,
                    'message': f'Setup product {product_id} tracked but does not grant Discord access',
                    'note': 'Setup products are for tracking only'
                }), 200
        
        # For remove_role/kick: Find the specific product row
        elif action in ['remove_role', 'kick']:
            # If product_id specified, find that specific row
            target_row = None
            if product_id:
                for row in user_rows:
                    if str(row['data'].get('Product ID', '')).strip() == product_id:
                        target_row = row
                        break
            else:
                # Default to first row if no product_id specified
                target_row = user_rows[0]
            
            if not target_row:
                return jsonify({
                    'error': f'Product ID {product_id} not found for email {email}',
                    'note': 'Check that the product_id matches what\'s in your sheet'
                }), 404
            
            # Check payment status
            payment_status = target_row['data'].get('Payment Status') or target_row['data'].get('Status', 'Unknown')
            payment_status_upper = payment_status.upper()
            
            # Only process removals if status is Refunded or Cancelled
            if payment_status_upper not in ['REFUNDED', 'CANCELLED']:
                return jsonify({
                    'error': f'Payment status is {payment_status}, must be REFUNDED or CANCELLED to remove access',
                    'note': 'Only refunded or cancelled orders trigger role removal'
                }), 400
            print(f"Processing {action} for {email} with status: {payment_status}")
            
            # Check if user verified their Discord
            discord_verified = target_row['data'].get('Discord Verified', '').lower()
            
            if discord_verified != 'yes':
                return jsonify({
                    'error': f'User {email} has not verified their Discord account yet',
                    'note': 'User needs to DM the bot with their email first'
                }), 400
            
            # Get Discord User ID from sheets
            discord_user_id = target_row['data'].get('Discord User ID', '')
        
        if not discord_user_id:
            return jsonify({
                'error': f'No Discord User ID stored for {email}',
                'note': 'User needs to verify via DM first'
            }), 400
        
        # Find the Discord user and process action
        bot.loop.create_task(handle_role_change_by_user_id(discord_user_id, action, email, product_id))
        
        return jsonify({
            'success': True,
            'email': email,
            'discord_user_id': discord_user_id,
            'action': action,
            'product_id': product_id or 'all',
            'payment_status': 'verified' if action == 'add_role' else payment_status
        }), 200
        
    except Exception as e:
        print(f"Webhook error: {e}")
        return jsonify({'error': str(e)}), 500

async def handle_role_change_by_user_id(discord_user_id, action, email, product_id=None):
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
        
        # Get user's product from sheets
        user_rows = find_all_user_rows(email)
        
        if action == 'add_role':
            # Add roles for all PAID products
            await assign_all_subscriber_roles(member, email)
            
            # Update ALL rows with Discord verification info (including the new product row)
            discord_username = f"{member.name}"
            update_discord_verified_status_all_rows(email, discord_username, str(member.id), True)
            
        elif action in ['remove_role', 'kick']:
            # Remove roles based on the specific product being cancelled
            if product_id:
                role_names = PRODUCT_ROLE_MAP.get(product_id)
            else:
                # If no product_id, remove all roles
                role_names = []
                for row in user_rows:
                    pid = str(row['data'].get('Product ID', '')).strip()
                    rnames = PRODUCT_ROLE_MAP.get(pid)
                    if rnames:
                        if isinstance(rnames, str):
                            role_names.append(rnames)
                        else:
                            role_names.extend(rnames)
                role_names = list(set(role_names))  # Remove duplicates
            
            # Handle as list
            if isinstance(role_names, str):
                role_names = [role_names]
            elif not role_names:
                role_names = ["Subscriber"]
            
            roles_to_modify = []
            
            # Get roles to remove
            for role_name in role_names:
                role = discord.utils.get(guild.roles, name=role_name)
                if role:
                    roles_to_modify.append(role)
            
            if action == 'remove_role':
                await member.remove_roles(*roles_to_modify)
                
                # Update sheets - mark as unverified
                user_row = next((r for r in user_rows if str(r['data'].get('Product ID', '')).strip() == product_id), user_rows[0])
                discord_username = user_row['data'].get('Discord Username', '') if user_row else ''
                update_discord_verified_status(email, discord_username, discord_user_id, False)
                
                try:
                    roles_text = ", ".join([f"**{r.name}**" for r in roles_to_modify])
                    await member.send(
                        f"Your subscription has been cancelled.\n"
                        f"The following roles have been removed: {roles_text}\n\n"
                        f"You can still hang out in the server! "
                        f"Rejoin anytime by resubscribing. 😊"
                    )
                except discord.Forbidden:
                    pass
                
                print(f"❌ Removed roles {[r.name for r in roles_to_modify]} from {member.name} ({email})")
                
            elif action == 'kick':
                await member.remove_roles(*roles_to_modify)
                
                # Update sheets
                user_row = next((r for r in user_rows if str(r['data'].get('Product ID', '')).strip() == product_id), user_rows[0])
                discord_username = user_row['data'].get('Discord Username', '') if user_row else ''
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
    from waitress import serve
    port = int(os.getenv('PORT', 8080))
    print(f"Starting Waitress production server on port {port}")
    serve(app, host='0.0.0.0', port=port)

if __name__ == '__main__':
    print("Starting bot with Google Sheets integration...")
    # Start Flask in a thread with Waitress (production server)
    Thread(target=run_flask).start()
    # Start Discord bot
    bot.run(os.getenv('DISCORD_TOKEN'))