import discord
from discord.ext import commands
from discord.ui import Button, View
import json, os, random, shutil, asyncio, psutil
import docker

DB_DIR = "./database"
CONFIG_FILE = "./config/config.json"
DB_FILE = f"{DB_DIR}/vps_plans.json"
VPS_DATA_DIR = f"{DB_DIR}/vpsdata"

os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(VPS_DATA_DIR, exist_ok=True)
os.makedirs("./config", exist_ok=True)

if not os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE,"w") as f:
        json.dump({"TOKEN":"YOUR_TOKEN_HERE","OWNER_ID":"YOUR_ID_HERE"},f,indent=4)
    print("Edit ./config/config.json with your token and owner ID")
    exit()

with open(CONFIG_FILE) as f:
    config = json.load(f)
TOKEN = config["TOKEN"]
OWNER_ID = int(config["OWNER_ID"])

client_docker = docker.from_env()

if os.path.exists(DB_FILE):
    with open(DB_FILE) as f:
        db = json.load(f)
else:
    db = {
        "plans":{
            "dirt":{"ram":"2g","storage":"10g"},
            "stone":{"ram":"2g","storage":"15g"},
            "gold":{"ram":"5g","storage":"25g"},
            "iron":{"ram":"10g","storage":"50g"},
            "diamond":{"ram":"15g","storage":"100g"},
            "netherite":{"ram":"50g","storage":"150g"}
        },
        "users":{},
        "vps_numbers":{}
    }

def save_db():
    with open(DB_FILE,"w") as f:
        json.dump(db,f,indent=4)

intents = discord.Intents.default()
bot = commands.Bot(command_prefix=".",intents=intents)

def container_name(user_id): return f"vps_{user_id}"
def user_data_folder(user_id):
    path = os.path.join(VPS_DATA_DIR,str(user_id))
    os.makedirs(path,exist_ok=True)
    return path

def start_container(user_id,plan):
    name = container_name(user_id)
    try:
        client_docker.containers.get(name)
        return
    except docker.errors.NotFound: pass
    data_path = user_data_folder(user_id)
    ram = db["plans"][plan]["ram"]
    client_docker.containers.run(
        "wither-vps:latest",
        name=name,
        detach=True,
        tty=True,
        mem_limit=ram,
        volumes={data_path:{'bind':'/vpsdata','mode':'rw'}},
        labels={"vps_user":str(user_id)}
    )

def stop_container(user_id):
    name = container_name(user_id)
    try:
        c = client_docker.containers.get(name)
        c.stop(timeout=5)
        c.remove(force=True)
    except docker.errors.NotFound:
        pass

def remove_tmate(user_id):
    try:
        c = client_docker.containers.get(container_name(user_id))
        c.exec_run(f"rm -f /tmp/tmate_{user_id}.sock", demux=True)
    except docker.errors.NotFound:
        pass

def create_tmate(user_id):
    try:
        c = client_docker.containers.get(container_name(user_id))
        c.exec_run(f"rm -f /tmp/tmate_{user_id}.sock", demux=True)
        c.exec_run(f"tmate -S /tmp/tmate_{user_id}.sock new-session -d", demux=True)
        for _ in range(10):
            result = c.exec_run(f"tmate -S /tmp/tmate_{user_id}.sock display -p '#{{tmate_ssh}}'", demux=True)
            output = result.output if hasattr(result,"output") else result[1]
            if output:
                ssh = output.decode().strip()
                if ssh: return ssh
            asyncio.sleep(1)
        result = c.exec_run(f"tmate -S /tmp/tmate_{user_id}.sock display -p '#{{tmate_ssh}}'", demux=True)
        output = result.output if hasattr(result,"output") else result[1]
        return output.decode().strip() if output else None
    except docker.errors.NotFound:
        return None

async def recover_startup():
    for user_id, data in db["users"].items():
        db["users"][user_id]["tmate_session"]=None
        save_db()
        remove_tmate(user_id)
        if data.get("status","stopped")=="running":
            try: start_container(user_id,data["plan"])
            except: db["users"][user_id]["status"]="stopped"
        else:
            stop_container(user_id)

@bot.event
async def on_ready():
    await recover_startup()
    print(f"{bot.user} ready")

@bot.command()
async def give(ctx,user:discord.Member,plan:str):
    if ctx.author.id!=OWNER_ID: return await ctx.send("Owner only")
    if plan not in db["plans"]: return await ctx.send("Plan invalid")
    vps_number=str(random.randint(10**9,10**10-1))
    while vps_number in db["vps_numbers"]:
        vps_number=str(random.randint(10**9,10**10-1))
    db["users"][str(user.id)]={"plan":plan,"status":"stopped","vps_number":vps_number,"tmate_session":None,"modules":[]}
    db["vps_numbers"][vps_number]=str(user.id)
    save_db()
    start_container(user.id,plan)
    db["users"][str(user.id)]["status"]="running"
    save_db()
    await ctx.send(f"{user.mention} given `{plan}` VPS #{vps_number}")

@bot.command()
async def manage(ctx):
    uid=str(ctx.author.id)
    data=db["users"].get(uid)
    if not data: return await ctx.send("No VPS assigned")
    plan=data["plan"]
    embed=discord.Embed(title="Wither VPS",color=discord.Color.blue())
    embed.add_field(name="Plan",value=plan)
    embed.add_field(name="RAM",value=db["plans"][plan]["ram"])
    embed.add_field(name="Storage",value=db["plans"][plan]["storage"])
    embed.add_field(name="VPS #",value=data["vps_number"])
    view=VPSView(ctx.author.id)
    await ctx.send(embed=embed,view=view)

class VPSView(View):
    def __init__(self,user_id):
        super().__init__(timeout=None)
        self.user_id=user_id

    @discord.ui.button(label="Start VPS",style=discord.ButtonStyle.green)
    async def start_button(self,interaction,button):
        if interaction.user.id!=self.user_id: return await interaction.response.send_message("Cannot manage this VPS",ephemeral=True)
        start_container(self.user_id,db["users"][str(self.user_id)]["plan"])
        db["users"][str(self.user_id)]["status"]="running"
        save_db()
        await interaction.response.send_message("VPS started ✅",ephemeral=True)

    @discord.ui.button(label="Stop VPS",style=discord.ButtonStyle.red)
    async def stop_button(self,interaction,button):
        if interaction.user.id!=self.user_id: return await interaction.response.send_message("Cannot manage this VPS",ephemeral=True)
        stop_container(self.user_id)
        db["users"][str(self.user_id)]["status"]="stopped"
        db["users"][str(self.user_id)]["tmate_session"]=None
        save_db()
        await interaction.response.send_message("VPS stopped ✅",ephemeral=True)

    @discord.ui.button(label="TMate SSH",style=discord.ButtonStyle.blurple)
    async def tmate_button(self,interaction,button):
        if interaction.user.id!=self.user_id: return await interaction.response.send_message("Cannot access this VPS",ephemeral=True)
        status=db["users"][str(self.user_id)].get("status","stopped")
        if status!="running": return await interaction.response.send_message("VPS not running",ephemeral=True)
        remove_tmate(self.user_id)
        ssh=create_tmate(self.user_id)
        if not ssh: return await interaction.response.send_message("Failed to create TMate session",ephemeral=True)
        db["users"][str(self.user_id)]["tmate_session"]=ssh
        save_db()
        try: await interaction.user.send(f"TMate SSH: `{ssh}`")
        except: pass
        await interaction.response.send_message("TMate SSH link DM’d ✅",ephemeral=True)

bot.run(TOKEN)
