import sys
sys.path=[p for p in sys.path if not p.endswith('/telegram')]

import telegram
from telegram.ext import Updater,CommandHandler,MessageHandler,Filters
from flask import Flask,request,jsonify,render_template,Response,stream_with_context,make_response
from flask_cors import CORS
import sqlite3,datetime,logging,os,threading
import requests as req_lib

TOKEN     =os.environ.get("BOT_TOKEN","6643550719:AAGsVoafrb3vyp57siDPKmi6tqBrYqTW7L8")
ADMIN_ID  =1347991918
def _detect_webapp_url():
    for d in os.environ.get('REPLIT_DOMAINS','').split(','):
        d=d.strip()
        if d.endswith('.replit.app'):return f'https://{d}'
    dev=os.environ.get('REPLIT_DEV_DOMAIN','')
    if dev:return f'https://{dev}'
    return 'http://localhost:5000'
WEBAPP_URL=os.environ.get("WEBAPP_URL") or _detect_webapp_url()

BOT_NAME     ="Elited Vip"
BOT_USERNAME =""  # /start sonrası doldurulur
FREE_DAYS =3

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',level=logging.INFO)
logger=logging.getLogger(__name__)

import threading
_DB_LOCAL=threading.local()
def get_db():
    if not hasattr(_DB_LOCAL,'conn') or _DB_LOCAL.conn is None:
        _DB_LOCAL.conn=sqlite3.connect('premium.db',check_same_thread=False)
    return _DB_LOCAL.conn,_DB_LOCAL.conn.cursor()

# Global for bot thread init
conn=sqlite3.connect('premium.db',check_same_thread=False)
c=conn.cursor()

def db_exec(sql,params=()):
    """Execute for bot thread."""
    try:
        c.execute(sql,params);conn.commit()
    except Exception as e:
        logger.warning(f"DB exec error: {e}")
        raise

def db_fetch(sql,params=(),fetch='all'):
    c.execute(sql,params)
    return c.fetchall() if fetch=='all' else c.fetchone()

for sql in[
"CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY,premium_date TEXT)",
"CREATE TABLE IF NOT EXISTS videos (id INTEGER PRIMARY KEY AUTOINCREMENT,category TEXT,file_id TEXT,title TEXT,channel_id TEXT,message_id INTEGER)",
"CREATE TABLE IF NOT EXISTS pending_payments (user_id INTEGER PRIMARY KEY,stars INTEGER,days INTEGER,package_name TEXT,date TEXT)",
"CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY AUTOINCREMENT,slug TEXT UNIQUE,label TEXT,emoji TEXT,parent_id INTEGER DEFAULT NULL)",
"CREATE TABLE IF NOT EXISTS new_users (user_id INTEGER PRIMARY KEY)",
"CREATE TABLE IF NOT EXISTS pending_video_uploads (admin_id INTEGER PRIMARY KEY,category TEXT,title TEXT,created_at TEXT)",
"CREATE TABLE IF NOT EXISTS packages (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,stars INTEGER,days INTEGER,active INTEGER DEFAULT 1)",
"CREATE TABLE IF NOT EXISTS video_views (id INTEGER PRIMARY KEY AUTOINCREMENT,video_id INTEGER,user_id INTEGER,category TEXT,viewed_at TEXT)",
"CREATE TABLE IF NOT EXISTS channel_settings (id INTEGER PRIMARY KEY,channel_id TEXT)",
"CREATE TABLE IF NOT EXISTS sent_videos (id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,video_id INTEGER,chat_message_id INTEGER,sent_at TEXT)",
"CREATE TABLE IF NOT EXISTS video_bundles (id INTEGER PRIMARY KEY AUTOINCREMENT,video_id INTEGER,file_id TEXT,file_type TEXT,sort_order INTEGER DEFAULT 0)",
"CREATE TABLE IF NOT EXISTS pending_bundle_uploads (admin_id INTEGER PRIMARY KEY,video_id INTEGER,created_at TEXT)",
"CREATE TABLE IF NOT EXISTS support_messages (id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,message TEXT,status TEXT DEFAULT 'open',created_at TEXT,reply_text TEXT,replied_at TEXT)",
"CREATE TABLE IF NOT EXISTS support_chat (id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,sender TEXT,message TEXT,sent_at TEXT)",
"CREATE TABLE IF NOT EXISTS referrals (id INTEGER PRIMARY KEY AUTOINCREMENT,referrer_id INTEGER,referred_id INTEGER UNIQUE,joined_at TEXT,join_rewarded INTEGER DEFAULT 0,purchase_rewarded INTEGER DEFAULT 0)",
]:
    c.execute(sql)

for mig in[
    "ALTER TABLE videos ADD COLUMN channel_id TEXT",
    "ALTER TABLE videos ADD COLUMN message_id INTEGER",
    "ALTER TABLE pending_payments ADD COLUMN days INTEGER DEFAULT 30",
    "ALTER TABLE pending_payments ADD COLUMN package_name TEXT DEFAULT 'Premium'",
    "ALTER TABLE videos ADD COLUMN thumb_file_id TEXT",
    "ALTER TABLE users ADD COLUMN first_name TEXT",
    "ALTER TABLE users ADD COLUMN username TEXT",
]:
    try:c.execute(mig);conn.commit()
    except:pass

c.execute("SELECT COUNT(*) FROM categories")
if c.fetchone()[0]==0:
    c.executemany("INSERT INTO categories (slug,label,emoji,parent_id) VALUES (?,?,?,?)",[
        ('film_dublaj','Filmler | Dublajl','\ud83c\udfa5',None),
        ('film_altyazi','Filmler | Altyazl','\ud83d\udcdd',None),
        ('dizi_dublaj','Diziler | Dublajl','\ud83d\udcfa',None),
        ('dizi_altyazi','Diziler | Altyazl','\ud83d\udcfa',None),
    ])

c.execute("SELECT COUNT(*) FROM packages")
if c.fetchone()[0]==0:
    c.executemany("INSERT INTO packages (name,stars,days,active) VALUES (?,?,?,1)",[
        ('7 G\u00fcn Premium',15,7),
        ('30 G\u00fcn Premium',50,30),
        ('90 G\u00fcn Premium',120,90),
    ])
conn.commit()

app=Flask(__name__)
CORS(app)
bot_instance=None

# ── helpers ──

def is_premium(user_id):
    c.execute("SELECT premium_date FROM users WHERE user_id=?",(user_id,))
    r=c.fetchone()
    if r:
        try:return datetime.datetime.strptime(r[0],'%Y-%m-%d')>datetime.datetime.now()
        except:return False
    return False

def days_remaining(user_id):
    c.execute("SELECT premium_date FROM users WHERE user_id=?",(user_id,))
    r=c.fetchone()
    if r:
        try:
            d=datetime.datetime.strptime(r[0],'%Y-%m-%d')
            return max((d-datetime.datetime.now()).days,0)
        except:pass
    return 0

def give_premium(user_id,days):
    c.execute("SELECT premium_date FROM users WHERE user_id=?",(user_id,))
    ex=c.fetchone()
    if ex:
        try:
            cur=datetime.datetime.strptime(ex[0],'%Y-%m-%d')
            base=cur if cur>datetime.datetime.now() else datetime.datetime.now()
        except:base=datetime.datetime.now()
    else:base=datetime.datetime.now()
    new=(base+datetime.timedelta(days=days)).strftime('%Y-%m-%d')
    c.execute("INSERT OR REPLACE INTO users (user_id,premium_date) VALUES (?,?)",(user_id,new))
    conn.commit()
    return new

def get_all_categories():
    c.execute("SELECT id,slug,label,emoji,parent_id FROM categories ORDER BY COALESCE(parent_id,0),label COLLATE NOCASE")
    return c.fetchall()

def get_categories(parent_id=None):
    if parent_id is None:
        c.execute("SELECT id,slug,label,emoji FROM categories WHERE parent_id IS NULL ORDER BY label COLLATE NOCASE")
    else:
        c.execute("SELECT id,slug,label,emoji FROM categories WHERE parent_id=? ORDER BY label COLLATE NOCASE",(parent_id,))
    return c.fetchall()

def get_video_counts():
    c.execute("SELECT category,COUNT(*) FROM videos GROUP BY category")
    return {(k if k is not None else ''):v for k,v in c.fetchall()}

def is_new_user(user_id):
    c.execute("SELECT 1 FROM new_users WHERE user_id=?",(user_id,))
    return c.fetchone() is None

def mark_user_seen(user_id):
    c.execute("INSERT OR IGNORE INTO new_users (user_id) VALUES (?)",(user_id,))
    conn.commit()

def get_active_packages():
    c.execute("SELECT id,name,stars,days FROM packages WHERE active=1 ORDER BY days")
    return c.fetchall()

def get_channel_id():
    c.execute("SELECT channel_id FROM channel_settings LIMIT 1")
    r=c.fetchone()
    return r[0] if r else None

def log_view(video_id,user_id,category):
    now=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute("INSERT INTO video_views (video_id,user_id,category,viewed_at) VALUES (?,?,?,?)",(video_id,user_id,category,now))
    conn.commit()

def _record_sent(user_id,video_id,chat_message_id):
    """Gonderilen video mesajini kaydet (silinebilmesi icin)."""
    now=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute("INSERT INTO sent_videos (user_id,video_id,chat_message_id,sent_at) VALUES (?,?,?,?)",(user_id,video_id,chat_message_id,now))
    conn.commit()

def delete_user_sent_videos(user_id):
    """Kullanicinin premium bittikten sonra bot tarafindan gonderilen videolari sil."""
    c.execute("SELECT chat_message_id FROM sent_videos WHERE user_id=?",(user_id,))
    rows=c.fetchall()
    deleted=0
    for (mid,) in rows:
        try:
            if bot_instance:
                bot_instance.delete_message(chat_id=user_id,message_id=mid)
                deleted+=1
        except Exception as e:
            logger.info(f"Mesaj silinemedi (zaten silinmis olabilir): user={user_id} mid={mid} err={e}")
    c.execute("DELETE FROM sent_videos WHERE user_id=?",(user_id,))
    conn.commit()
    logger.info(f"Kullanici sent_videos temizlendi: user={user_id} silindi={deleted}/{len(rows)}")
    return deleted,len(rows)

def get_view_stats():
    c.execute("SELECT video_id,COUNT(DISTINCT user_id) FROM video_views GROUP BY video_id ORDER BY COUNT(*) DESC LIMIT 20")
    top_videos=c.fetchall()
    c.execute("SELECT category,COUNT(*) FROM video_views GROUP BY category ORDER BY COUNT(*) DESC")
    top_cats=c.fetchall()
    c.execute("SELECT COUNT(DISTINCT user_id) FROM video_views")
    unique_viewers=c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM video_views")
    total_views=c.fetchone()[0]
    c.execute("SELECT strftime('%Y-%m-%d',viewed_at) as d,COUNT(*) FROM video_views GROUP BY d ORDER BY d DESC LIMIT 7")
    daily=c.fetchall()
    return {'top_videos':top_videos,'top_categories':top_cats,'unique_viewers':unique_viewers,'total_views':total_views,'daily':daily}

def _menu_btn():
    return telegram.InlineKeyboardButton("🏠 Ana Menü",callback_data="menu_main")

def with_menu(rows):
    """Verilen satır listesinin sonuna 🏠 Ana Menü butonu ekler ve InlineKeyboardMarkup döndürür."""
    rows=list(rows)
    has=any(
        any(getattr(b,'callback_data',None)=='menu_main' for b in row)
        for row in rows
    )
    if not has:
        rows.append([_menu_btn()])
    return telegram.InlineKeyboardMarkup(rows)

def build_nav(user_id,is_main=False):
    rows=[]
    if not is_main:rows.append([telegram.InlineKeyboardButton("🏠 Ana Menü",callback_data="menu_main")])
    if not is_premium(user_id):rows.append([telegram.InlineKeyboardButton("⭐ Premium Ol",web_app=telegram.WebAppInfo(url=WEBAPP_URL+"?page=premium"))])
    return telegram.InlineKeyboardMarkup(rows)

# ── user app ──

@app.route('/')
def user_app():return render_template('app.html')

@app.route('/api/user/home')
def api_user_home():
    db,cur=get_db()
    try:
        user_id=int(request.args.get('user_id',0))
    except:return jsonify({'error':'invalid'}),400
    premium=is_premium(user_id);rem=days_remaining(user_id) if premium else 0
    counts=get_video_counts()
    cur.execute("SELECT COUNT(*) FROM videos");tv=cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE premium_date > ?",(datetime.datetime.now().strftime('%Y-%m-%d'),));tm=cur.fetchone()[0]
    cats=[]
    for cid,slug,label,emoji,parent_id in get_all_categories():
        children=get_categories(parent_id=cid)
        sub_count=sum(counts.get(ch[1],0) for ch in children)
        total_in=counts.get(slug,0)+sub_count
        cats.append({'id':cid,'slug':slug,'label':label,'emoji':emoji,'parent_id':parent_id,'count':total_in,'has_children':len(children)>0})
    pkgs=get_active_packages()
    cur.execute("SELECT id,title,category,thumb_file_id FROM videos ORDER BY id DESC LIMIT 500")
    all_vids=[{'id':r[0],'title':r[1],'category':r[2],'thumb_file_id':r[3]} for r in cur.fetchall()]
    return jsonify({'premium':premium,'days_remaining':rem,'total_videos':tv,'total_members':tm,'categories':cats,
                    'packages':[{'id':p[0],'name':p[1],'stars':p[2],'days':p[3]} for p in pkgs],
                    'new_user_gift':is_new_user(user_id) if user_id else False,'free_days':FREE_DAYS,
                    'all_videos':all_vids})

@app.route('/api/user/videos')
def api_user_videos():
    db,cur=get_db()
    try:user_id=int(request.args.get('user_id',0));slug=request.args.get('category','')
    except:return jsonify({'error':'invalid'}),400
    if not slug:return jsonify({'error':'category required'}),400
    premium=is_premium(user_id)
    cur.execute("SELECT id,slug,label,emoji,parent_id FROM categories WHERE slug=?",(slug,))
    cat=cur.fetchone()
    if not cat:return jsonify({'error':'not found'}),404
    children=get_categories(parent_id=cat[0])
    if children:
        counts=get_video_counts()
        subcats=[{'id':ch[0],'slug':ch[1],'label':ch[2],'emoji':ch[3],'count':counts.get(ch[1],0)} for ch in children]
        return jsonify({'type':'subcategories','label':cat[2],'emoji':cat[3],'subcategories':subcats})
    cur.execute("SELECT id,title,thumb_file_id FROM videos WHERE category=?",(slug,))
    videos=[{'id':r[0],'title':r[1],'locked':not premium,'thumb':r[2]} for r in cur.fetchall()]
    return jsonify({'type':'videos','label':cat[2],'emoji':cat[3],'videos':videos,'premium':premium})

@app.route('/api/user/send-video/<int:video_id>',methods=['POST'])
def api_send_video(video_id):
    """Videoyu bot araciligiyla kullaniciya gonder (protect_content=True)."""
    db,cur=get_db()
    try:user_id=int(request.args.get('user_id',0))
    except:return jsonify({'ok':False,'error':'invalid'}),400
    if not user_id:return jsonify({'ok':False,'error':'user_id gerekli'}),400
    if not is_premium(user_id):
        return jsonify({'ok':False,'premium_required':True,'error':'Premium uyelik gerekli. Premium alarak tum videolara erisebilirsiniz.'})
    cur.execute("SELECT file_id,title,category,channel_id,message_id FROM videos WHERE id=?",(video_id,))
    row=cur.fetchone()
    if not row:return jsonify({'ok':False,'error':'Video bulunamadi'}),404
    file_id,title,cat,channel_id,message_id=row
    if not bot_instance:return jsonify({'ok':False,'error':'Bot hazir degil'}),503
    log_view(video_id,user_id,cat)
    # Bundle iceriklerini once gonder (resimler once, sonra ekstra videolar)
    cur.execute("SELECT file_id,file_type FROM video_bundles WHERE video_id=? ORDER BY CASE WHEN file_type='photo' THEN 0 ELSE 1 END,sort_order",(video_id,))
    bundle_items=cur.fetchall()
    for b_fid,b_type in bundle_items:
        try:
            if b_type=='photo':
                bm=bot_instance.send_photo(chat_id=user_id,photo=b_fid,protect_content=True)
            else:
                bm=bot_instance.send_video(chat_id=user_id,video=b_fid,protect_content=True)
            _record_sent(user_id,video_id,bm.message_id)
        except Exception as be:
            logger.warning(f"Bundle item gonderilemedi: {be}")
    # Kanal mesaji varsa forward_message ile gonder (protect_content destekli)
    if channel_id and message_id:
        try:
            sent_msg=bot_instance.forward_message(
                chat_id=user_id,
                from_chat_id=channel_id,
                message_id=int(message_id),
                protect_content=True
            )
            _record_sent(user_id,video_id,sent_msg.message_id)
            logger.info(f"Video gonderildi (forward, korumal): video_id={video_id} user={user_id}")
            return jsonify({'ok':True,'method':'forward','title':title})
        except Exception as e:
            logger.warning(f"Forward failed, copy_message deneniyor: {e}")
            # forward basarisizsa copy_message dene
            try:
                copy_msg=bot_instance.copy_message(
                    chat_id=user_id,
                    from_chat_id=channel_id,
                    message_id=int(message_id),
                    protect_content=True
                )
                _record_sent(user_id,video_id,copy_msg.message_id)
                return jsonify({'ok':True,'method':'copy','title':title})
            except Exception as e2:
                logger.warning(f"copy_message also failed: {e2}")
    # Fallback: file_id ile direkt gonder
    try:
        sv_msg=bot_instance.send_video(
            chat_id=user_id,
            video=file_id,
            caption=f"*{title}*",
            parse_mode='Markdown',
            protect_content=True
        )
        _record_sent(user_id,video_id,sv_msg.message_id)
        logger.info(f"Video gonderildi (send_video, korumal): video_id={video_id} user={user_id}")
        return jsonify({'ok':True,'method':'send','title':title})
    except Exception as e:
        logger.error(f"Video gonderilemedi: video_id={video_id} user={user_id} err={e}")
        return jsonify({'ok':False,'error':str(e)}),500

def save_user_info(user_id,first_name=None,username=None):
    try:
        db,cur=get_db()
        cur.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)",(user_id,))
        if first_name:cur.execute("UPDATE users SET first_name=? WHERE user_id=?",(first_name,user_id))
        if username:cur.execute("UPDATE users SET username=? WHERE user_id=?",(username,user_id))
        db.commit()
    except:pass

@app.route('/api/user/register-gift',methods=['POST'])
def api_user_register_gift():
    data=request.get_json();user_id=data.get('user_id');fn=data.get('first_name','Üye');un=data.get('username')
    if not user_id:return jsonify({'success':False})
    save_user_info(user_id,fn,un)
    if not is_new_user(user_id):return jsonify({'success':False,'already_seen':True})
    mark_user_seen(user_id);pd=give_premium(user_id,FREE_DAYS)
    if bot_instance:
        try:bot_instance.send_message(user_id,f"\ud83c\udf89 *{BOT_NAME}'e Ho\u015f Geldiniz, {fn}!*\n\n\ud83c\udf81 *{FREE_DAYS} g\u00fcn \u00fccretsiz premium*!\n\ud83d\udcc5 Biti\u015f: {pd}",parse_mode='Markdown')
        except:pass
    return jsonify({'success':True,'premium_date':pd,'free_days':FREE_DAYS})

@app.route('/api/user/create-invoice',methods=['POST'])
def api_create_invoice():
    """Telegram Stars fatura linki olustur — tg.openInvoice() icin."""
    data=request.get_json();user_id=data.get('user_id');package_id=data.get('package_id')
    if not user_id:return jsonify({'ok':False,'error':'user_id gerekli'})
    db,cur=get_db()
    if package_id:
        cur.execute("SELECT id,name,stars,days FROM packages WHERE id=? AND active=1",(package_id,))
        pkg=cur.fetchone()
    else:
        pkgs=get_active_packages()
        pkg=pkgs[0] if pkgs else None
    if not pkg:return jsonify({'ok':False,'error':'Paket bulunamadi'})
    pid,pname,pstars,pdays=pkg
    # Telegram Bot API — createInvoiceLink (Stars: currency=XTR, provider_token="")
    payload=f"pkg_{pid}_uid_{user_id}"
    tg_resp=req_lib.post(
        f"https://api.telegram.org/bot{TOKEN}/createInvoiceLink",
        json={
            "title":f"{BOT_NAME} — {pname}",
            "description":f"{pdays} gun premium uyelik. Otomatik aktif olur.",
            "payload":payload,
            "provider_token":"",
            "currency":"XTR",
            "prices":[{"label":pname,"amount":pstars}]
        },timeout=15
    )
    resp=tg_resp.json()
    if not resp.get('ok'):
        logger.error(f"createInvoiceLink hata: {resp}")
        return jsonify({'ok':False,'error':resp.get('description','Invoice olusturulamadi')})
    link=resp['result']
    logger.info(f"Invoice olusturuldu: user={user_id} pkg={pname} stars={pstars}")
    return jsonify({'ok':True,'invoice_link':link,'package_name':pname,'stars':pstars,'days':pdays})

@app.route('/api/user/buy-premium',methods=['POST'])
def api_user_buy_premium():
    """Eski manuel onay akisi — yedek olarak tutuldu."""
    data=request.get_json();user_id=data.get('user_id');package_id=data.get('package_id')
    if not user_id:return jsonify({'success':False,'error':'user_id gerekli'})
    if package_id:
        c.execute("SELECT name,stars,days FROM packages WHERE id=? AND active=1",(package_id,))
        pkg=c.fetchone()
    else:
        pkgs=get_active_packages()
        pkg=(pkgs[0][1],pkgs[0][2],pkgs[0][3]) if pkgs else ('Premium',50,30)
    if not pkg:return jsonify({'success':False,'error':'Paket bulunamad\u0131'})
    pn,ps,pd=pkg
    c.execute("INSERT OR REPLACE INTO pending_payments (user_id,stars,days,package_name,date) VALUES (?,?,?,?,?)",(user_id,ps,pd,pn,datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    if bot_instance:
        try:bot_instance.send_message(ADMIN_ID,f"\ud83d\udcb0 *Yeni \u00d6deme Talebi!*\n\n\ud83d\udc64 ID: `{user_id}`\n\ud83d\udce6 Paket: {pn}\n\u2b50 Stars: {ps}\n\ud83d\udcc5 G\u00fcn: {pd}\n\n/admin ile paneli a\u00e7\u0131n.",parse_mode='Markdown')
        except:pass
    return jsonify({'success':True,'package_name':pn,'stars':ps,'days':pd})

# ── admin app ──

@app.route('/admin')
def admin_panel():return render_template('admin.html')

@app.route('/api/stats')
def api_stats():
    db,cur=get_db()
    now=datetime.datetime.now().strftime('%Y-%m-%d')
    cur.execute("SELECT COUNT(*) FROM users");tu=cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE premium_date > ?",(now,));pu=cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM videos");tv=cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM pending_payments");pp=cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM categories");tc=cur.fetchone()[0]
    stats=get_view_stats()
    return jsonify({'total_users':tu,'premium_users':pu,'total_videos':tv,'pending_payments':pp,'total_cats':tc,'views':stats})

@app.route('/api/videos/<int:video_id>/bundle')
def api_get_bundle(video_id):
    db,cur=get_db()
    cur.execute("SELECT id,file_type,sort_order FROM video_bundles WHERE video_id=? ORDER BY CASE WHEN file_type='photo' THEN 0 ELSE 1 END,sort_order",(video_id,))
    items=[{'id':r[0],'file_type':r[1],'sort_order':r[2]} for r in cur.fetchall()]
    return jsonify({'items':items,'count':len(items)})

@app.route('/api/bundle/<int:bundle_id>',methods=['DELETE'])
def api_delete_bundle_item(bundle_id):
    db,cur=get_db()
    cur.execute("DELETE FROM video_bundles WHERE id=?",(bundle_id,))
    db.commit()
    return jsonify({'success':True})

@app.route('/api/videos/<int:video_id>/bundle/clear',methods=['DELETE'])
def api_clear_bundle(video_id):
    db,cur=get_db()
    cur.execute("DELETE FROM video_bundles WHERE video_id=?",(video_id,))
    db.commit()
    return jsonify({'success':True})

@app.route('/api/bundles/all')
def api_get_all_bundles():
    db,cur=get_db()
    cur.execute("""SELECT vb.id,vb.video_id,vb.file_type,vb.sort_order,v.title
                   FROM video_bundles vb
                   LEFT JOIN videos v ON vb.video_id=v.id
                   ORDER BY vb.video_id, CASE WHEN vb.file_type='photo' THEN 0 ELSE 1 END, vb.sort_order""",[])
    rows=cur.fetchall()
    return jsonify(success=True,items=[{'id':r[0],'video_id':r[1],'file_type':r[2],'sort_order':r[3],'video_title':r[4] or '—'} for r in rows])

@app.route('/api/bundle/<int:bundle_id>/assign',methods=['POST'])
def api_assign_bundle(bundle_id):
    data=request.get_json() or {}
    vid=data.get('video_id')
    if not vid:return jsonify(success=False,error='video_id gerekli')
    db,cur=get_db()
    cur.execute("UPDATE video_bundles SET video_id=? WHERE id=?",(vid,bundle_id))
    db.commit()
    return jsonify(success=True)

@app.route('/api/users')
def api_users():
    db,cur=get_db()
    cur.execute("SELECT user_id,premium_date,first_name,username FROM users ORDER BY premium_date DESC")
    now=datetime.datetime.now()
    return jsonify({'users':[{'user_id':r[0],'premium_date':r[1],'first_name':r[2],'username':r[3],'active':datetime.datetime.strptime(r[1],'%Y-%m-%d')>now if r[1] else False} for r in cur.fetchall()]})

@app.route('/api/users/<int:user_id>')
def api_user_detail(user_id):
    db,cur=get_db()
    cur.execute("SELECT user_id,premium_date FROM users WHERE user_id=?",(user_id,))
    row=cur.fetchone()
    if not row:return jsonify({'found':False})
    try:active=datetime.datetime.strptime(row[1],'%Y-%m-%d')>datetime.datetime.now()
    except:active=False
    return jsonify({'found':True,'user':{'user_id':row[0],'premium_date':row[1],'active':active}})

@app.route('/api/users/premium',methods=['POST'])
def api_give_premium():
    data=request.get_json();uid=data.get('user_id');days=data.get('days',30)
    if not uid:return jsonify({'success':False,'error':'user_id gerekli'})
    pd=give_premium(uid,days)
    if bot_instance:
        try:bot_instance.send_message(uid,f"\ud83c\udf89 *{BOT_NAME} Premium!*\n\n\u2705 {days} g\u00fcn eklendi.\n\ud83d\udcc5 Biti\u015f: {pd}",parse_mode='Markdown')
        except:pass
    return jsonify({'success':True,'premium_date':pd})

@app.route('/api/users/<int:user_id>/revoke',methods=['POST'])
def api_revoke_premium(user_id):
    db,cur=get_db()
    cur.execute("DELETE FROM users WHERE user_id=?",(user_id,));db.commit()
    # Gonderilen videolari sil
    threading.Thread(target=delete_user_sent_videos,args=(user_id,),daemon=True).start()
    if bot_instance:
        try:bot_instance.send_message(user_id,f"\u274c *{BOT_NAME} premium iptal edildi.*\n\nGonderilen videolar sohbetinizden silindi.",parse_mode='Markdown')
        except:pass
    return jsonify({'success':True})

@app.route('/api/videos')
def api_videos():
    db,cur=get_db()
    cur.execute("SELECT id,category,title,thumb_file_id FROM videos ORDER BY id DESC")
    counts=get_video_counts()
    return jsonify({'videos':[{'id':r[0],'category':r[1],'title':r[2],'thumb':r[3]} for r in cur.fetchall()],'counts':counts})

@app.route('/api/videos/<int:video_id>',methods=['DELETE'])
def api_delete_video(video_id):
    db,cur=get_db()
    cur.execute("DELETE FROM videos WHERE id=?",(video_id,));db.commit()
    return jsonify({'success':True})

@app.route('/api/videos/prepare',methods=['POST'])
def api_prepare_video():
    data=request.get_json();cat=data.get('category','').strip();title=data.get('title','').strip()
    if not cat or not title:return jsonify({'success':False,'error':'Eksik bilgi'})
    db,cur=get_db()
    cur.execute("SELECT 1 FROM categories WHERE slug=?",(cat,))
    if not cur.fetchone():return jsonify({'success':False,'error':'Ge\u00e7ersiz kategori'})
    cur.execute("INSERT OR REPLACE INTO pending_video_uploads (admin_id,category,title,created_at) VALUES (?,?,?,?)",(ADMIN_ID,cat,title,datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    db.commit()
    if bot_instance:
        try:bot_instance.send_message(ADMIN_ID,f"\ud83d\udce4 *Video Y\u00fckleme Haz\u0131r!*\n\n\ud83d\udcc2 `{cat}`\n\ud83c\udfac *{title}*\n\n\u015eimdi videoyu g\u00f6nderin \ud83d\udc47",parse_mode='Markdown')
        except:pass
    return jsonify({'success':True})

@app.route('/api/payments')
def api_payments():
    db,cur=get_db()
    cur.execute("SELECT user_id,stars,days,package_name,date FROM pending_payments ORDER BY date DESC")
    return jsonify({'payments':[{'user_id':r[0],'stars':r[1],'days':r[2],'package_name':r[3],'date':r[4]} for r in cur.fetchall()]})

@app.route('/api/payments/<int:user_id>/approve',methods=['POST'])
def api_approve_payment(user_id):
    db,cur=get_db()
    cur.execute("SELECT stars,days,package_name FROM pending_payments WHERE user_id=?",(user_id,))
    row=cur.fetchone()
    if not row:return jsonify({'success':False,'error':'\u00d6deme bulunamad\u0131'})
    stars,days,pn=row[0],row[1] or 30,row[2] or 'Premium'
    pd=give_premium(user_id,days)
    cur.execute("DELETE FROM pending_payments WHERE user_id=?",(user_id,));db.commit()
    try:process_referral_purchase(user_id,days)
    except:pass
    if bot_instance:
        try:
            nav=build_nav(user_id)
            bot_instance.send_message(user_id,f"\u2705 *\u00d6demeniz onayland\u0131!* \ud83c\udf89\n\n\ud83d\udce6 {pn}\n\u2b50 {stars} Stars\n\ud83d\udcc5 Biti\u015f: {pd}",parse_mode='Markdown',reply_markup=nav)
        except:pass
    return jsonify({'success':True})

@app.route('/api/payments/<int:user_id>/reject',methods=['POST'])
def api_reject_payment(user_id):
    db,cur=get_db()
    cur.execute("DELETE FROM pending_payments WHERE user_id=?",(user_id,));db.commit()
    if bot_instance:
        try:
            nav=build_nav(user_id)
            bot_instance.send_message(user_id,"\u274c *\u00d6deme talebiniz reddedildi.*",parse_mode='Markdown',reply_markup=nav)
        except:pass
    return jsonify({'success':True})

@app.route('/api/user/support',methods=['POST'])
def api_user_support():
    data=request.get_json() or {}
    user_id=data.get('user_id');message=data.get('message','').strip()
    if not user_id or not message:return jsonify(success=False,error='Eksik alan')
    db,cur=get_db()
    now=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur.execute("INSERT INTO support_chat (user_id,sender,message,sent_at) VALUES (?,'user',?,?)",(user_id,message,now))
    db.commit()
    if bot_instance:
        try:
            bot_instance.send_message(ADMIN_ID,
                f"📩 *Yeni Destek Mesajı!*\n\n👤 ID: `{user_id}`\n💬 {message}\n\n"
                f"Yanıtlamak için:\n`/yanit {user_id} mesajınız`",parse_mode='Markdown')
        except:pass
    return jsonify(success=True)

@app.route('/api/user/support-history')
def api_user_support_history():
    uid=request.args.get('user_id',type=int)
    if not uid:return jsonify(success=False,error='user_id gerekli')
    db,cur=get_db()
    cur.execute("SELECT sender,message,sent_at FROM support_chat WHERE user_id=? ORDER BY sent_at ASC",(uid,))
    rows=cur.fetchall()
    return jsonify(success=True,messages=[{'sender':r[0],'message':r[1],'sent_at':r[2]} for r in rows])

@app.route('/api/support')
def api_support_list():
    db,cur=get_db()
    cur.execute("SELECT user_id FROM support_chat GROUP BY user_id ORDER BY MAX(sent_at) DESC")
    users=[r[0] for r in cur.fetchall()]
    threads=[]
    for uid in users:
        cur.execute("SELECT sender,message,sent_at FROM support_chat WHERE user_id=? ORDER BY sent_at ASC",(uid,))
        msgs=[{'sender':r[0],'message':r[1],'sent_at':r[2]} for r in cur.fetchall()]
        last=msgs[-1] if msgs else {}
        unread=sum(1 for m in msgs if m['sender']=='user')
        cur.execute("SELECT first_name,username FROM users WHERE user_id=?",(uid,))
        urow=cur.fetchone()
        name=(urow[0] if urow and urow[0] else None) or (('@'+urow[1]) if urow and urow[1] else None) or str(uid)
        threads.append({'user_id':uid,'name':name,'messages':msgs,'last_at':last.get('sent_at',''),'unread':unread})
    return jsonify(success=True,threads=threads)

@app.route('/api/support/<int:user_id>/reply',methods=['POST'])
def api_support_reply(user_id):
    data=request.get_json() or {}
    reply=data.get('reply','').strip()
    if not reply:return jsonify(success=False,error='Yanıt boş')
    db,cur=get_db()
    now=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur.execute("INSERT INTO support_chat (user_id,sender,message,sent_at) VALUES (?,'admin',?,?)",(user_id,reply,now))
    db.commit()
    if bot_instance:
        try:
            kb=[[telegram.InlineKeyboardButton(f"📩 Destekten Yanıt",web_app=telegram.WebAppInfo(url=WEBAPP_URL+'?page=destek'))]]
            bot_instance.send_message(user_id,f"📩 *Destek Ekibinden Yanıt*\n\n{reply}",parse_mode='Markdown',reply_markup=telegram.InlineKeyboardMarkup(kb))
        except Exception as e:return jsonify(success=False,error=str(e))
    return jsonify(success=True)

@app.route('/api/support/<int:user_id>',methods=['DELETE'])
def api_support_delete(user_id):
    db,cur=get_db()
    cur.execute("DELETE FROM support_chat WHERE user_id=?",(user_id,))
    db.commit();return jsonify(success=True)

@app.route('/api/user/referral')
def api_user_referral():
    uid=request.args.get('user_id',type=int)
    if not uid:return jsonify(success=False,error='user_id gerekli')
    db,cur=get_db()
    cur.execute("SELECT COUNT(*),SUM(join_rewarded),SUM(purchase_rewarded) FROM referrals WHERE referrer_id=?",(uid,))
    row=cur.fetchone()
    total=row[0] or 0;join_r=row[1] or 0;purchase_r=row[2] or 0
    link=f"https://t.me/{BOT_USERNAME}?start=ref_{uid}" if BOT_USERNAME else ''
    return jsonify(success=True,link=link,total_referred=total,join_rewards=join_r,purchase_rewards=purchase_r)

@app.route('/api/admin/gift-all',methods=['POST'])
def api_gift_all():
    data=request.get_json() or {}
    days=int(data.get('days',30))
    if days<1 or days>3650:return jsonify(success=False,error='Geçersiz gün')
    db,cur=get_db()
    cur.execute("SELECT user_id FROM users")
    users=cur.fetchall()
    now=datetime.datetime.now()
    count=0
    for (uid,) in users:
        cur.execute("SELECT premium_date FROM users WHERE user_id=?",(uid,))
        row=cur.fetchone()
        if row and row[0]:
            try:current=datetime.datetime.strptime(row[0],'%Y-%m-%d')
            except:current=now
            base=max(current,now)
        else:base=now
        new_date=(base+datetime.timedelta(days=days)).strftime('%Y-%m-%d')
        cur.execute("UPDATE users SET premium_date=? WHERE user_id=?",(new_date,uid))
        count+=1
    db.commit()
    if bot_instance:
        try:
            bot_instance.send_message(ADMIN_ID,f"🎁 *Toplu Hediye Tamamlandı!*\n\n👥 {count} kullanıcıya {days} gün premium eklendi.",parse_mode='Markdown')
        except:pass
    return jsonify(success=True,count=count,days=days)

@app.route('/api/broadcast',methods=['POST'])
def api_broadcast():
    data=request.get_json();msg=data.get('message')
    if not msg:return jsonify({'success':False,'error':'Mesaj bo\u015f'})
    db,cur=get_db()
    now=datetime.datetime.now().strftime('%Y-%m-%d')
    cur.execute("SELECT user_id FROM users WHERE premium_date > ?",(now,))
    sent=0
    for (uid,) in cur.fetchall():
        if bot_instance:
            try:
                nav=build_nav(uid)
                bot_instance.send_message(uid,f"\ud83d\udce2 *{BOT_NAME} Duyuru*\n\n{msg}",parse_mode='Markdown',reply_markup=nav)
                sent+=1
            except:pass
    return jsonify({'success':True,'sent':sent})

@app.route('/api/clean-expired',methods=['POST'])
def api_clean_expired():
    db,cur=get_db()
    now=datetime.datetime.now().strftime('%Y-%m-%d')
    cur.execute("SELECT user_id FROM users WHERE premium_date <= ?",(now,))
    expired_users=[r[0] for r in cur.fetchall()]
    cnt=len(expired_users)
    cur.execute("DELETE FROM users WHERE premium_date <= ?",(now,));db.commit()
    # Her biten kullanicinin videolarini arka planda sil
    def cleanup_all():
        for uid in expired_users:
            delete_user_sent_videos(uid)
            if bot_instance:
                try:bot_instance.send_message(uid,f"\u274c *{BOT_NAME} Premiumunuz sona erdi.*\n\nGonderilen videolar sohbetinizden silindi. Yenilemek icin uygulamayi acin.",parse_mode='Markdown')
                except:pass
    threading.Thread(target=cleanup_all,daemon=True).start()
    return jsonify({'success':True,'cleaned':cnt})

@app.route('/api/categories')
def api_categories():
    db,cur=get_db()
    cur.execute("SELECT id,slug,label,emoji,parent_id FROM categories ORDER BY COALESCE(parent_id,0),label COLLATE NOCASE")
    counts=get_video_counts()
    return jsonify({'categories':[{'id':r[0],'slug':r[1],'label':r[2],'emoji':r[3],'parent_id':r[4],'count':counts.get(r[1],0)} for r in cur.fetchall()]})

@app.route('/api/categories',methods=['POST'])
def api_add_category():
    data=request.get_json();slug=data.get('slug','').strip().replace(' ','_').lower();label=data.get('label','').strip();emoji=data.get('emoji','\ud83d\udcc1').strip();parent_id=data.get('parent_id') or None
    if not slug or not label:return jsonify({'success':False,'error':'slug ve label gerekli'})
    db,cur=get_db()
    try:cur.execute("INSERT INTO categories (slug,label,emoji,parent_id) VALUES (?,?,?,?)",(slug,label,emoji,parent_id));db.commit();return jsonify({'success':True})
    except sqlite3.IntegrityError:return jsonify({'success':False,'error':'Bu slug zaten mevcut'})

@app.route('/api/categories/<int:cat_id>',methods=['PUT'])
def api_update_category(cat_id):
    data=request.get_json();label=data.get('label','').strip();emoji=data.get('emoji','').strip()
    if not label:return jsonify({'success':False,'error':'label gerekli'})
    db,cur=get_db()
    cur.execute("UPDATE categories SET label=?,emoji=? WHERE id=?",(label,emoji,cat_id));db.commit();return jsonify({'success':True})

@app.route('/api/categories/<int:cat_id>',methods=['DELETE'])
def api_delete_category(cat_id):
    db,cur=get_db()
    cur.execute("SELECT slug FROM categories WHERE id=?",(cat_id,));row=cur.fetchone()
    if not row:return jsonify({'success':False,'error':'Bulunamad\u0131'})
    cur.execute("SELECT COUNT(*) FROM videos WHERE category=?",(row[0],))
    if cur.fetchone()[0]>0:return jsonify({'success':False,'error':'\u0130\u00e7inde video var'})
    cur.execute("SELECT COUNT(*) FROM categories WHERE parent_id=?",(cat_id,))
    if cur.fetchone()[0]>0:return jsonify({'success':False,'error':'Alt kategoriler var'})
    cur.execute("DELETE FROM categories WHERE id=?",(cat_id,));db.commit();return jsonify({'success':True})

@app.route('/api/packages')
def api_packages():
    db,cur=get_db()
    cur.execute("SELECT id,name,stars,days,active FROM packages ORDER BY days")
    return jsonify({'packages':[{'id':r[0],'name':r[1],'stars':r[2],'days':r[3],'active':bool(r[4])} for r in cur.fetchall()]})

@app.route('/api/packages',methods=['POST'])
def api_add_package():
    data=request.get_json();name=data.get('name','').strip();stars=int(data.get('stars',0));days=int(data.get('days',0))
    if not name or not stars or not days:return jsonify({'success':False,'error':'Ad, Stars ve G\u00fcn gerekli'})
    db,cur=get_db()
    cur.execute("INSERT INTO packages (name,stars,days,active) VALUES (?,?,?,1)",(name,stars,days));db.commit();return jsonify({'success':True})

@app.route('/api/packages/<int:pkg_id>',methods=['PUT'])
def api_update_package(pkg_id):
    data=request.get_json();name=data.get('name','').strip();stars=int(data.get('stars',0));days=int(data.get('days',0));active=1 if data.get('active',True) else 0
    if not name or not stars or not days:return jsonify({'success':False,'error':'Eksik bilgi'})
    db,cur=get_db()
    cur.execute("UPDATE packages SET name=?,stars=?,days=?,active=? WHERE id=?",(name,stars,days,active,pkg_id));db.commit();return jsonify({'success':True})

@app.route('/api/packages/<int:pkg_id>',methods=['DELETE'])
def api_delete_package(pkg_id):
    db,cur=get_db()
    cur.execute("DELETE FROM packages WHERE id=?",(pkg_id,));db.commit();return jsonify({'success':True})

# ── channel ──

@app.route('/api/channel',methods=['GET','POST'])
def api_channel():
    db,cur=get_db()
    if request.method=='GET':
        cur.execute("SELECT channel_id FROM channel_settings LIMIT 1")
        r=cur.fetchone();ch=r[0] if r else None
        return jsonify({'channel_id':ch,'set':bool(ch)})
    data=request.get_json();ch_id=data.get('channel_id','').strip()
    if not ch_id:return jsonify({'success':False,'error':'Kanal ID gerekli'})
    cur.execute("DELETE FROM channel_settings")
    cur.execute("INSERT INTO channel_settings (id,channel_id) VALUES (1,?)",(ch_id,))
    db.commit()
    return jsonify({'success':True})

@app.route('/api/videos/uncategorized')
def api_uncategorized_videos():
    db,cur=get_db()
    cur.execute("SELECT id,file_id,title,channel_id,message_id,thumb_file_id FROM videos WHERE category IS NULL ORDER BY id DESC")
    rows=cur.fetchall()
    return jsonify({'videos':[{'id':r[0],'file_id':r[1],'title':r[2] or 'Video #'+str(r[0]),'channel_id':r[3],'message_id':r[4],'thumb':r[5]} for r in rows]})

@app.route('/api/videos/<int:video_id>/categorize',methods=['POST'])
def api_categorize_video(video_id):
    db,cur=get_db()
    data=request.get_json();slug=data.get('category','').strip()
    if not slug:return jsonify({'success':False,'error':'Kategori gerekli'})
    cur.execute("SELECT 1 FROM categories WHERE slug=?",(slug,))
    if not cur.fetchone():return jsonify({'success':False,'error':'Ge\u00e7ersiz kategori'})
    cur.execute("UPDATE videos SET category=? WHERE id=?",(slug,video_id))
    db.commit()
    # Tüm kullanıcılara bildirim gönder
    cur.execute("SELECT title FROM videos WHERE id=?",(video_id,))
    vrow=cur.fetchone()
    cur.execute("SELECT label,emoji,parent_id FROM categories WHERE slug=?",(slug,))
    crow=cur.fetchone()
    if vrow and crow and bot_instance:
        parent_label=None
        if crow[2]:
            cur.execute("SELECT label,emoji FROM categories WHERE id=?",(crow[2],))
            prow=cur.fetchone()
            if prow:parent_label=prow[1]+' '+prow[0]
        cat_line=(parent_label+' › ' if parent_label else '')+crow[1]+' '+crow[0]
        cur.execute("SELECT user_id FROM users")
        all_users=[r[0] for r in cur.fetchall()]
        kb=[[telegram.InlineKeyboardButton(f"🎬 İzle",web_app=telegram.WebAppInfo(url=WEBAPP_URL))]]
        markup=telegram.InlineKeyboardMarkup(kb)
        msg=f"🆕 Yeni Video!\n\n🎬 *{vrow[0]}*\n📂 {cat_line}\n\nHemen izlemek için aşağıdan açın 👇"
        import threading
        def broadcast():
            for uid in all_users:
                try:bot_instance.send_message(uid,msg,parse_mode='Markdown',reply_markup=markup)
                except:pass
        threading.Thread(target=broadcast,daemon=True).start()
    return jsonify({'success':True})

@app.route('/api/videos/<int:video_id>/title',methods=['POST'])
def api_set_video_title(video_id):
    db,cur=get_db()
    data=request.get_json();title=data.get('title','').strip()
    if not title:return jsonify({'success':False,'error':'Baslik gerekli'})
    cur.execute("SELECT id FROM videos WHERE id=?",(video_id,))
    if not cur.fetchone():return jsonify({'success':False,'error':'Video bulunamadi'})
    cur.execute("UPDATE videos SET title=? WHERE id=?",(title,video_id))
    db.commit()
    return jsonify({'success':True})

@app.route('/api/thumb/<int:video_id>')
def api_thumb(video_id):
    """Video thumbnail'ini Telegram'dan cekip dondur."""
    db,cur=get_db()
    try:user_id=int(request.args.get('user_id',0))
    except:return jsonify({'ok':False,'error':'invalid'}),400
    if not user_id:return jsonify({'ok':False,'error':'user_id gerekli'}),400
    if not is_premium(user_id):return jsonify({'ok':False,'error':'premium gerekli'}),403
    cur.execute("SELECT thumb_file_id,file_id FROM videos WHERE id=?",(video_id,))
    row=cur.fetchone()
    if not row:return jsonify({'ok':False,'error':'video bulunamadi'}),404
    thumb_id,vid_id=row
    fid=thumb_id or vid_id
    if not fid:return jsonify({'ok':False,'error':'thumb yok'}),404
    try:
        tf=bot_instance.get_file(fid)
        import requests
        r=requests.get(tf.file_path,timeout=15)
        if r.status_code!=200:return jsonify({'ok':False,'error':'thumb indirilemedi'}),500
        resp=make_response(r.content)
        resp.headers.set('Content-Type','image/jpeg')
        resp.headers.set('Cache-Control','private, max-age=86400')
        return resp
    except Exception as e:
        logger.warning(f"Thumb hatasi vid={video_id}: {e}")
        return jsonify({'ok':False,'error':'thumb hatasi'}),500

# ── bot handlers ──

def process_referral_join(referred_id,referrer_id):
    """Yeni kullanıcı ref ile geldi — referrer'a +1 gün ver."""
    global BOT_USERNAME
    if referred_id==referrer_id:return
    try:
        now=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute("INSERT OR IGNORE INTO referrals (referrer_id,referred_id,joined_at,join_rewarded) VALUES (?,?,?,0)",(referrer_id,referred_id,now))
        if c.rowcount==0:return  # Zaten kayıtlı
        give_premium(referrer_id,1)
        c.execute("UPDATE referrals SET join_rewarded=1 WHERE referred_id=?",(referred_id,))
        conn.commit()
        if bot_instance:
            try:bot_instance.send_message(referrer_id,f"🎁 *Referans Ödülü!*\n\nDavet ettiğiniz bir kişi bota katıldı.\n✅ *+1 gün premium* hesabınıza eklendi!",parse_mode='Markdown')
            except:pass
    except Exception as e:logger.error(f"Referral join error: {e}")

def process_referral_purchase(buyer_id,pkg_days):
    """Referans ile gelen kullanıcı paket aldı — referrer'a yarı kadar gün ver."""
    try:
        c.execute("SELECT referrer_id,purchase_rewarded FROM referrals WHERE referred_id=?",(buyer_id,))
        row=c.fetchone()
        if not row:return
        referrer_id,already=row
        bonus=max(1,pkg_days//2)
        give_premium(referrer_id,bonus)
        c.execute("UPDATE referrals SET purchase_rewarded=purchase_rewarded+1 WHERE referred_id=?",(buyer_id,))
        conn.commit()
        if bot_instance:
            try:bot_instance.send_message(referrer_id,f"💰 *Referans Alım Ödülü!*\n\nDavet ettiğiniz biri paket satın aldı.\n✅ *+{bonus} gün premium* hesabınıza eklendi!",parse_mode='Markdown')
            except:pass
    except Exception as e:logger.error(f"Referral purchase error: {e}")

# ══════════════════════════════════════════
# INLINE MENU SİSTEMİ
# ══════════════════════════════════════════

def main_menu_kb(uid):
    prem=is_premium(uid)
    rows=[
        [
            telegram.InlineKeyboardButton("📁 Kategoriler",callback_data="menu_cats:0"),
            telegram.InlineKeyboardButton("⭐ Durumum",callback_data="menu_prem"),
        ],
        [
            telegram.InlineKeyboardButton("🔗 Referans",callback_data="menu_ref"),
            telegram.InlineKeyboardButton("💬 Destek",callback_data="menu_destek"),
        ],
    ]
    if not prem:
        rows.append([telegram.InlineKeyboardButton("🛒 Premium Satın Al",callback_data="menu_buy")])
    rows.append([telegram.InlineKeyboardButton(f"🔥 {BOT_NAME}'i Aç (Mini App)",web_app=telegram.WebAppInfo(url=WEBAPP_URL))])
    return telegram.InlineKeyboardMarkup(rows)

def _root_cat_counts():
    """Root kategori başına toplam video sayısı (alt kategoriler üzerinden)."""
    db,cur=get_db()
    cur.execute('''
        SELECT p.slug,COUNT(v.id)
        FROM categories p
        LEFT JOIN categories ch ON ch.parent_id=p.id
        LEFT JOIN videos v ON v.category=ch.slug
        WHERE p.parent_id IS NULL
        GROUP BY p.id
    ''')
    return {r[0]:r[1] for r in cur.fetchall()}

def cats_kb(page=0,per_page=8):
    cats=get_categories()  # root kategoriler
    counts=_root_cat_counts()
    total=len(cats)
    start_i=page*per_page
    end_i=min(start_i+per_page,total)
    chunk=cats[start_i:end_i]
    rows=[]
    for i in range(0,len(chunk),2):
        row=[]
        for cat in chunk[i:i+2]:
            cid,slug,label,emoji=cat
            cnt=counts.get(slug,0)
            row.append(telegram.InlineKeyboardButton(
                f"{emoji or '📁'} {label} ({cnt})",
                callback_data=f"rootcat:{slug}"
            ))
        rows.append(row)
    nav=[]
    if page>0:nav.append(telegram.InlineKeyboardButton("◀️ Önceki",callback_data=f"menu_cats:{page-1}"))
    if end_i<total:nav.append(telegram.InlineKeyboardButton("Sonraki ▶️",callback_data=f"menu_cats:{page+1}"))
    if nav:rows.append(nav)
    return with_menu(rows)

def subcats_kb(root_slug):
    """Bir root kategorinin alt kategorilerini listeler."""
    db,cur=get_db()
    cur.execute("SELECT id FROM categories WHERE slug=?",(root_slug,))
    row=cur.fetchone()
    if not row:return None,[]
    parent_id=row[0]
    cur.execute("SELECT id,slug,label,emoji FROM categories WHERE parent_id=? ORDER BY label COLLATE NOCASE",(parent_id,))
    subcats=cur.fetchall()
    # her alt katin video sayisi
    cur.execute('''
        SELECT ch.slug,COUNT(v.id) FROM categories ch
        LEFT JOIN videos v ON v.category=ch.slug
        WHERE ch.parent_id=? GROUP BY ch.id
    ''',(parent_id,))
    cnts={r[0]:r[1] for r in cur.fetchall()}
    rows=[]
    for sc in subcats:
        _,slug,label,emoji=sc
        cnt=cnts.get(slug,0)
        rows.append([telegram.InlineKeyboardButton(
            f"{emoji or '🎬'} {label} ({cnt} video)",
            callback_data=f"subcat:{slug}:0"
        )])
    rows.append([telegram.InlineKeyboardButton("📁 Kategoriler",callback_data="menu_cats:0")])
    return with_menu(rows),subcats

def _videos_in_cat(slug,page=0,per_page=6):
    db,cur=get_db()
    cur.execute("SELECT id,title FROM videos WHERE category=? ORDER BY id DESC LIMIT ? OFFSET ?",(slug,per_page,page*per_page))
    videos=cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM videos WHERE category=?",(slug,))
    total=cur.fetchone()[0]
    return videos,total

def videos_kb(slug,page=0,uid=None,per_page=6):
    prem=is_premium(uid) if uid else False
    videos,total=_videos_in_cat(slug,page,per_page)
    rows=[]
    for vid_id,title in videos:
        # başlıktan tag'leri temizle
        clean=title.split('\n')[0].split('#')[0].strip()
        short=clean[:30]+"…" if len(clean)>32 else clean
        if prem:
            rows.append([telegram.InlineKeyboardButton(f"▶️ {short}",callback_data=f"sendvid:{vid_id}")])
        else:
            rows.append([telegram.InlineKeyboardButton(f"🔒 {short}",callback_data="menu_buy")])
    nav=[]
    if page>0:nav.append(telegram.InlineKeyboardButton("◀️ Önceki",callback_data=f"subcat:{slug}:{page-1}"))
    if (page+1)*per_page<total:nav.append(telegram.InlineKeyboardButton("Sonraki ▶️",callback_data=f"subcat:{slug}:{page+1}"))
    if nav:rows.append(nav)
    rows.append([telegram.InlineKeyboardButton("📁 Kategoriler",callback_data="menu_cats:0")])
    return with_menu(rows),videos,total

def packages_kb():
    pkgs=get_active_packages()
    rows=[]
    for pid,name,stars,days in pkgs:
        rows.append([telegram.InlineKeyboardButton(
            f"⭐ {stars} Stars → {name} ({days} gün)",
            callback_data=f"buypkg:{pid}"
        )])
    return with_menu(rows)

def handle_callback(update,context):
    q=update.callback_query
    uid=q.from_user.id
    data=q.data
    q.answer()

    # Ana Menü
    if data=="menu_main":
        q.edit_message_text(
            f"🔥 *{BOT_NAME} — Ana Menü*\n\nAşağıdan bir işlem seç:",
            parse_mode='Markdown',
            reply_markup=main_menu_kb(uid)
        )
        return

    # Kategoriler listesi
    elif data.startswith("menu_cats:"):
        page=int(data.split(":")[1])
        cats=get_categories()
        q.edit_message_text(
            f"📁 *Kategoriler* ({len(cats)} kategori)\n\nBir kategori seç:",
            parse_mode='Markdown',
            reply_markup=cats_kb(page)
        )

    # Root kategori → alt kategorileri göster
    elif data.startswith("rootcat:"):
        root_slug=data.split(":")[1]
        db,cur=get_db()
        cur.execute("SELECT label,emoji FROM categories WHERE slug=?",(root_slug,))
        row=cur.fetchone()
        label=row[0] if row else root_slug
        emoji=(row[1] or '📁') if row else '📁'
        kb,subcats=subcats_kb(root_slug)
        if not subcats:
            # Alt kategori yok, direkt video göster
            kb2,videos,total=videos_kb(root_slug,0,uid)
            prem=is_premium(uid)
            status="✅ Premium" if prem else "🔒 Premium gerekli"
            vids_text="".join(f"\n{'▶️' if prem else '🔒'} {v[1].split(chr(10))[0].split('#')[0].strip()}" for v in videos) or "\n_Bu kategoride video bulunamadı._"
            q.edit_message_text(
                f"{emoji} *{label}*\n📊 {total} video | {status}{vids_text}",
                parse_mode='Markdown',reply_markup=kb2
            )
        else:
            q.edit_message_text(
                f"{emoji} *{label}*\n\nAlt kategori seç:",
                parse_mode='Markdown',reply_markup=kb
            )

    # Alt kategori → videoları göster
    elif data.startswith("subcat:"):
        parts=data.split(":")
        slug=parts[1]
        page=int(parts[2]) if len(parts)>2 else 0
        prem=is_premium(uid)
        kb,videos,total=videos_kb(slug,page,uid)
        db,cur=get_db()
        cur.execute("SELECT label,emoji,parent_id FROM categories WHERE slug=?",(slug,))
        row=cur.fetchone()
        label=row[0] if row else slug
        emoji=(row[1] or '🎬') if row else '🎬'
        parent_id=row[2] if row else None
        # root kategori adını bul (geri butonu için)
        root_slug=slug
        if parent_id:
            cur.execute("SELECT slug FROM categories WHERE id=?",(parent_id,))
            pr=cur.fetchone()
            if pr:root_slug=pr[0]
        status="✅ Premium" if prem else "🔒 Premium gerekli"
        vids_text=""
        for vid_id,title in videos:
            clean=title.split('\n')[0].split('#')[0].strip()
            ic='▶️' if prem else '🔒'
            vids_text+=f"\n{ic} {clean}"
        if not videos:vids_text="\n_Bu kategoride video bulunamadı._"
        # Geri butonu ekle (root categoriye)
        back_row=[telegram.InlineKeyboardButton(f"⬅️ Geri",callback_data=f"rootcat:{root_slug}")]
        if kb.inline_keyboard:
            last=kb.inline_keyboard[-1]
            kb.inline_keyboard.insert(-1,[back_row[0]])
        txt=(
            f"{emoji} *{label}*\n"
            f"📊 {total} video | Sayfa {page+1} | {status}"
            f"{vids_text}"
        )
        q.edit_message_text(txt,parse_mode='Markdown',reply_markup=kb)

    # Video gönder
    elif data.startswith("sendvid:"):
        vid_id=int(data.split(":")[1])
        if not is_premium(uid):
            q.edit_message_text(
                "🔒 *Bu video premium üyelere özeldir.*\n\nPremium almak için:",
                parse_mode='Markdown',
                reply_markup=packages_kb()
            )
            return
        db,cur=get_db()
        cur.execute("SELECT title,file_id,category FROM videos WHERE id=?",(vid_id,))
        row=cur.fetchone()
        if not row:
            q.edit_message_text("❌ Video bulunamadı.",reply_markup=main_menu_kb(uid))
            return
        title,file_id,category=row
        log_view(vid_id,uid,category)
        try:
            sent=bot_instance.send_video(
                uid,video=file_id,
                caption=f"🎬 *{title}*\n\n_{BOT_NAME}_",
                parse_mode='Markdown'
            )
            _record_sent(uid,vid_id,sent.message_id)
            q.edit_message_text(
                f"✅ *{title}* gönderildi!",
                parse_mode='Markdown',
                reply_markup=with_menu([
                    [telegram.InlineKeyboardButton("📁 Kategorilere Dön",callback_data="menu_cats:0")],
                ])
            )
        except Exception as e:
            q.edit_message_text(f"❌ Gönderilemedi: {e}",reply_markup=main_menu_kb(uid))

    # Premium durumu
    elif data=="menu_prem":
        prem=is_premium(uid)
        if prem:
            rem=days_remaining(uid)
            txt=(
                f"⭐ *Premium Durumun*\n\n"
                f"✅ Aktif — *{rem} gün* kaldı\n\n"
                f"Premium üyeliğin süresince tüm içeriklere erişebilirsin."
            )
            kb=with_menu([[telegram.InlineKeyboardButton("🛒 Süre Uzat",callback_data="menu_buy")]])
        else:
            txt=(
                f"🔒 *Premium Durumun*\n\n"
                f"❌ Aktif premium üyeliğin yok.\n\n"
                f"Premium alarak tüm içeriklere sansürsüz erişebilirsin."
            )
            kb=with_menu([[telegram.InlineKeyboardButton("⭐ Premium Al",callback_data="menu_buy")]])
        q.edit_message_text(txt,parse_mode='Markdown',reply_markup=kb)

    # Paket listesi
    elif data=="menu_buy":
        pkgs=get_active_packages()
        if not pkgs:
            q.edit_message_text("⚠️ Şu an aktif paket bulunmuyor.",reply_markup=with_menu([]))
            return
        txt="🛒 *Premium Paketler*\n\nTelegram Stars ile ödeme yap:\n"
        for pid,name,stars,days in pkgs:
            txt+=f"\n⭐ {stars} Stars → *{name}* ({days} gün)"
        q.edit_message_text(txt,parse_mode='Markdown',reply_markup=packages_kb())

    # Stars faturası oluştur
    elif data.startswith("buypkg:"):
        pkg_id=int(data.split(":")[1])
        db,cur=get_db()
        cur.execute("SELECT name,stars,days FROM packages WHERE id=? AND active=1",(pkg_id,))
        pkg=cur.fetchone()
        if not pkg:
            q.answer("❌ Paket bulunamadı!",show_alert=True)
            return
        name,stars,days=pkg
        try:
            bot_instance.send_invoice(
                chat_id=uid,
                title=f"⭐ {name}",
                description=f"{BOT_NAME} — {days} gün premium erişim",
                payload=f"premium_{uid}_{pkg_id}_{days}",
                provider_token="",
                currency="XTR",
                prices=[telegram.LabeledPrice(label=name,amount=stars)],
            )
            q.edit_message_text(
                f"✅ *{name}* için ödeme talebi gönderildi!\n\nTelegram üzerinden Stars ile ödeme yap.",
                parse_mode='Markdown',
                reply_markup=with_menu([])
            )
        except Exception as e:
            q.answer(f"❌ Hata: {e}",show_alert=True)

    # Referans
    elif data=="menu_ref":
        try:bot_un=bot_instance.get_me().username or BOT_USERNAME
        except:bot_un=BOT_USERNAME
        ref_link=f"https://t.me/{bot_un}?start=ref_{uid}"
        db,cur=get_db()
        cur.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=?",(uid,))
        ref_count=cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=? AND join_rewarded=1",(uid,))
        rewarded=cur.fetchone()[0]
        txt=(
            f"🔗 *Referans Linkin*\n\n"
            f"`{ref_link}`\n\n"
            f"👥 Davet ettiğin: *{ref_count} kişi*\n"
            f"🎁 Ödül aldığın: *{rewarded} kişi*\n\n"
            f"📌 *Kurallar:*\n"
            f"• Davet ettiğin kişi katılırsa → +1 gün\n"
            f"• Davet ettiğin kişi paket alırsa → paketin yarısı kadar gün"
        )
        import urllib.parse
        share_text=urllib.parse.quote(f"{BOT_NAME}'e katıl, ücretsiz premium kazan!")
        share_url=urllib.parse.quote(ref_link)
        kb=with_menu([[telegram.InlineKeyboardButton("📤 Linki Paylaş",url=f"https://t.me/share/url?url={share_url}&text={share_text}")]])
        q.edit_message_text(txt,parse_mode='Markdown',reply_markup=kb)

    # Destek
    elif data=="menu_destek":
        txt=(
            f"💬 *Destek*\n\n"
            f"Sorun veya öneriniz için:\n\n"
            f"📱 Mini App → Destek sekmesine yaz\n"
            f"📩 Direkt bot'a mesaj at, ekibimiz yanıtlar\n\n"
            f"_Yanıt süresi: 24 saat içinde_"
        )
        kb=with_menu([[telegram.InlineKeyboardButton("📱 Mini App — Destek",web_app=telegram.WebAppInfo(url=WEBAPP_URL+"?page=destek"))]])
        q.edit_message_text(txt,parse_mode='Markdown',reply_markup=kb)

def duyuru_cmd(update,context):
    """Admin duyurusu: /duyuru <mesaj> — tüm kullanıcılara gönderir."""
    if update.effective_user.id!=ADMIN_ID:return update.message.reply_text("❌ Sadece admin.")
    args=context.args
    if not args:
        return update.message.reply_text(
            "📢 *Duyuru Komutu*\n\n"
            "Kullanım:\n"
            "`/duyuru <mesaj>` → Tüm kullanıcılara\n"
            "`/duyuruvip <mesaj>` → Yalnızca premium üyelere",
            parse_mode='Markdown'
        )
    msg_text=' '.join(args)
    db,cur=get_db()
    cur.execute("SELECT user_id,first_name FROM users")
    all_users=cur.fetchall()
    status_msg=update.message.reply_text(f"⏳ Gönderiliyor... 0/{len(all_users)}")
    sent=0;fail=0
    for i,(uid,fn) in enumerate(all_users):
        try:
            bot_instance.send_message(
                uid,
                f"📢 *{BOT_NAME} — Duyuru*\n\n{msg_text}",
                parse_mode='Markdown',
                reply_markup=with_menu([])
            )
            sent+=1
        except:
            fail+=1
        if (i+1)%10==0:
            try:status_msg.edit_text(f"⏳ Gönderiliyor... {i+1}/{len(all_users)}")
            except:pass
    status_msg.edit_text(
        f"✅ *Duyuru Tamamlandı!*\n\n"
        f"👥 Toplam: {len(all_users)}\n"
        f"✅ Gönderildi: {sent}\n"
        f"❌ Başarısız: {fail}",
        parse_mode='Markdown'
    )

def duyuruvip_cmd(update,context):
    """Admin duyurusu: /duyuruvip <mesaj> — yalnızca premium üyelere gönderir."""
    if update.effective_user.id!=ADMIN_ID:return update.message.reply_text("❌ Sadece admin.")
    args=context.args
    if not args:return update.message.reply_text("Kullanım: `/duyuruvip <mesaj>`",parse_mode='Markdown')
    msg_text=' '.join(args)
    db,cur=get_db()
    now=datetime.datetime.now().strftime('%Y-%m-%d')
    cur.execute("SELECT user_id,first_name FROM users WHERE premium_date > ?",(now,))
    prem_users=cur.fetchall()
    if not prem_users:
        return update.message.reply_text("⚠️ Şu an aktif premium kullanıcı yok.")
    status_msg=update.message.reply_text(f"⏳ VIP duyurusu gönderiliyor... 0/{len(prem_users)}")
    sent=0;fail=0
    for i,(uid,fn) in enumerate(prem_users):
        try:
            bot_instance.send_message(
                uid,
                f"⭐ *{BOT_NAME} — VIP Duyuru*\n\n{msg_text}",
                parse_mode='Markdown',
                reply_markup=with_menu([])
            )
            sent+=1
        except:
            fail+=1
        if (i+1)%10==0:
            try:status_msg.edit_text(f"⏳ VIP duyurusu gönderiliyor... {i+1}/{len(prem_users)}")
            except:pass
    status_msg.edit_text(
        f"✅ *VIP Duyurusu Tamamlandı!*\n\n"
        f"👑 Premium üye: {len(prem_users)}\n"
        f"✅ Gönderildi: {sent}\n"
        f"❌ Başarısız: {fail}",
        parse_mode='Markdown'
    )

def menu_cmd(update,context):
    uid=update.effective_user.id
    fn=update.effective_user.first_name or "VIP"
    save_user_info(uid,fn,update.effective_user.username)
    update.message.reply_text(
        f"🔥 *{BOT_NAME} — Ana Menü*\n\nMerhaba, {fn}! Aşağıdan bir işlem seç:",
        parse_mode='Markdown',
        reply_markup=main_menu_kb(uid)
    )

def start(update,context):
    uid=update.effective_user.id;fn=update.effective_user.first_name or "VIP";un=update.effective_user.username
    save_user_info(uid,fn,un)
    is_new=is_new_user(uid)
    # Referral deep link işle: /start ref_12345
    args=context.args
    if is_new and args and args[0].startswith('ref_'):
        try:
            referrer_id=int(args[0][4:])
            process_referral_join(uid,referrer_id)
        except:pass
    prem=is_premium(uid)
    if is_new:
        t=(
            f"🔥 *Hoşgeldin, {fn}!*\n\n"
            f"*{BOT_NAME}*'e katıldın — Telegram'ın en özel içerikleri burada.\n\n"
            f"🎁 *{FREE_DAYS} gün ücretsiz premium* hediye!\n"
            f"Aşağıdaki butonlardan içeriklere göz at.\n\n"
            f"⚠️ Bu bot yalnızca *18+* kullanıcılara yöneliktir."
        )
    elif prem:
        rem=days_remaining(uid)
        t=(
            f"🔥 *Hoşgeldin, {fn}!*\n\n"
            f"✅ Premium üyeliğin aktif — *{rem} gün* kaldı.\n\n"
            f"Aşağıdaki butonlardan içeriklere erişebilirsin."
        )
    else:
        t=(
            f"🔥 *Hoşgeldin, {fn}!*\n\n"
            f"*{BOT_NAME}* — Telegram'ın en büyük içerik arşivi.\n\n"
            f"🔒 *Binlerce özel video*, sansürsüz erişim\n"
            f"⚡ Ödeme yapar yapmaz anında aktif\n"
            f"🛡️ İndirme korumalı — tamamen gizli\n\n"
            f"⭐ Premium al, şimdi erişim kazan.\n\n"
            f"⚠️ *18+ içeriktir.* Devam ederek yetişkin olduğunu onaylıyorsun."
        )
    update.message.reply_text(t,parse_mode='Markdown',reply_markup=main_menu_kb(uid))

def admin_cmd(update,context):
    if update.effective_user.id!=ADMIN_ID:return update.message.reply_text("\u274c Sadece admin.")
    kb=[[telegram.InlineKeyboardButton("\ud83d\udee0\ufe0f Admin Panel",web_app=telegram.WebAppInfo(url=WEBAPP_URL+"/admin"))]]
    update.message.reply_text(f"\ud83d\udd10 *{BOT_NAME} Admin Paneli*",parse_mode='Markdown',reply_markup=telegram.InlineKeyboardMarkup(kb))

def setchannel_cmd(update,context):
    if update.effective_user.id!=ADMIN_ID:return update.message.reply_text("\u274c Sadece admin.")
    args=context.args
    if not args:return update.message.reply_text("\u274c Kullan\u0131m: /setchannel -100xxxxxxx")
    ch_id=args[0].strip()
    c.execute("DELETE FROM channel_settings")
    c.execute("INSERT INTO channel_settings (id,channel_id) VALUES (1,?)",(ch_id,))
    conn.commit()
    update.message.reply_text(f"\u2705 Kanal ayarland\u0131: `{ch_id}`\n\nArt\u0131k videolar bu kanala y\u00fcklenecek.\nAdmin panelinden 'Kanal'dan \u00c7ek' ile i\u00e7erikleri i\u015fleyin.",parse_mode='Markdown')

def bundle_cmd(update,context):
    if update.effective_user.id!=ADMIN_ID:return update.message.reply_text("\u274c Sadece admin.")
    args=context.args
    if not args:
        c.execute("SELECT id,title FROM videos ORDER BY id DESC LIMIT 10")
        rows=c.fetchall()
        txt="\ud83d\udce6 Kullan\u0131m: /bundle <video_id>\n\nSon 10 video:\n"+"\n".join([f"#{r[0]} \u2014 {r[1]}" for r in rows])
        return update.message.reply_text(txt)
    try:vid=int(args[0])
    except:return update.message.reply_text("\u274c Ge\u00e7ersiz video ID.")
    c.execute("SELECT title FROM videos WHERE id=?",(vid,))
    vrow=c.fetchone()
    if not vrow:return update.message.reply_text(f"\u274c Video #{vid} bulunamad\u0131.")
    c.execute("INSERT OR REPLACE INTO pending_bundle_uploads (admin_id,video_id,created_at) VALUES (?,?,?)",(ADMIN_ID,vid,datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    c.execute("SELECT COUNT(*) FROM video_bundles WHERE video_id=?",(vid,))
    cnt=c.fetchone()[0]
    update.message.reply_text(f"\ud83d\udce6 *Bundle Modu A\u00e7\u0131k!*\n\n\ud83c\udfac Video: {vrow[0]} (#{vid})\n\ud83d\udcca Mevcut: {cnt} \u00f6\u011fe\n\n\ud83d\udcf8 Resim veya video g\u00f6nderin \u2192 bundle'a eklenir\n\u2705 Bitince /donebundle yaz\u0131n",parse_mode='Markdown')

def donebundle_cmd(update,context):
    if update.effective_user.id!=ADMIN_ID:return update.message.reply_text("\u274c Sadece admin.")
    c.execute("SELECT video_id FROM pending_bundle_uploads WHERE admin_id=?",(ADMIN_ID,))
    brow=c.fetchone()
    if not brow:return update.message.reply_text("\u26a0\ufe0f Aktif bundle modu yok.")
    vid=brow[0]
    c.execute("SELECT COUNT(*) FROM video_bundles WHERE video_id=?",(vid,))
    cnt=c.fetchone()[0]
    c.execute("DELETE FROM pending_bundle_uploads WHERE admin_id=?",(ADMIN_ID,))
    conn.commit()
    update.message.reply_text(f"\u2705 Bundle tamamland\u0131!\n\ud83c\udfac Video #{vid}\n\ud83d\udce6 Toplam: {cnt} \u00f6\u011fe\n\nKullan\u0131c\u0131lar bu videoya t\u0131klay\u0131nca t\u00fcm \u00f6\u011feler g\u00f6nderilecek.")

def handle_photo(update,context):
    uid=update.effective_user.id
    if uid!=ADMIN_ID:return
    if not update.message.photo:return
    c.execute("SELECT video_id FROM pending_bundle_uploads WHERE admin_id=?",(ADMIN_ID,))
    brow=c.fetchone()
    if not brow:return
    vid=brow[0]
    fid=update.message.photo[-1].file_id
    c.execute("SELECT MAX(sort_order) FROM video_bundles WHERE video_id=?",(vid,))
    mx=c.fetchone()[0] or 0
    c.execute("INSERT INTO video_bundles (video_id,file_id,file_type,sort_order) VALUES (?,?,?,?)",(vid,fid,'photo',mx+1))
    conn.commit()
    c.execute("SELECT COUNT(*) FROM video_bundles WHERE video_id=?",(vid,))
    cnt=c.fetchone()[0]
    update.message.reply_text(f"\ud83d\udcf8 Resim eklendi! Toplam: {cnt} \u00f6\u011fe  (/donebundle ile tamamlay\u0131n)")

def handle_video(update,context):
    uid=update.effective_user.id
    if uid!=ADMIN_ID or not update.message.video:return
    # Bundle modu aktifse, video'yu bundle'a ekle
    c.execute("SELECT video_id FROM pending_bundle_uploads WHERE admin_id=?",(ADMIN_ID,))
    brow=c.fetchone()
    if brow:
        vid=brow[0];fid=update.message.video.file_id
        c.execute("SELECT MAX(sort_order) FROM video_bundles WHERE video_id=?",(vid,))
        mx=c.fetchone()[0] or 0
        c.execute("INSERT INTO video_bundles (video_id,file_id,file_type,sort_order) VALUES (?,?,?,?)",(vid,fid,'video',mx+1))
        conn.commit()
        c.execute("SELECT COUNT(*) FROM video_bundles WHERE video_id=?",(vid,))
        cnt=c.fetchone()[0]
        update.message.reply_text(f"\ud83c\udfac Video eklendi! Toplam: {cnt} \u00f6\u011fe  (/donebundle ile tamamlay\u0131n)")
        return
    c.execute("SELECT category,title FROM pending_video_uploads WHERE admin_id=?",(ADMIN_ID,))
    row=c.fetchone()
    if row:
        cat,title=row;fid=update.message.video.file_id
        ch=get_channel_id()
        if ch:
            try:
                fwd=bot_instance.send_video(chat_id=ch,video=fid,caption=title)
                tid=fwd.video.thumb.file_id if fwd.video and fwd.video.thumb else None
                c.execute("INSERT INTO videos (category,file_id,title,channel_id,message_id,thumb_file_id) VALUES (?,?,?,?,?,?)",(cat,fid,title,str(ch),fwd.message_id,tid))
            except Exception as e:
                c.execute("INSERT INTO videos (category,file_id,title,thumb_file_id) VALUES (?,?,?,?)",(cat,fid,title,tid))
        else:
            c.execute("INSERT INTO videos (category,file_id,title,thumb_file_id) VALUES (?,?,?,?)",(cat,fid,title,tid))
        c.execute("DELETE FROM pending_video_uploads WHERE admin_id=?",(ADMIN_ID,))
        conn.commit()
        update.message.reply_text(f"\u2705 Video eklendi!\n\ud83d\udcc2 {cat}\n\ud83c\udfac {title}")
    elif 'yukle' in context.user_data:
        data=context.user_data['yukle'];fid=update.message.video.file_id
        tid=update.message.video.thumb.file_id if update.message.video and update.message.video.thumb else None
        c.execute("INSERT INTO videos (category,file_id,title,thumb_file_id) VALUES (?,?,?,?)",(data['category'],fid,data['title'],tid))
        conn.commit();del context.user_data['yukle']
        update.message.reply_text(f"\u2705 Video eklendi!\n\ud83d\udcc2 {data['category']}\n\ud83c\udfac {data['title']}")
    else:
        ch=get_channel_id()
        if ch:
            try:
                fwd=bot_instance.send_video(chat_id=ch,video=update.message.video.file_id)
                update.message.reply_text(f"\u2705 Video kanala g\u00f6nderildi!\nKanal: {ch}\nMesaj ID: {fwd.message_id}\n\nAdmin panelinden 'Kanal'dan \u00c7ek' ile kategori atay\u0131n.")
            except Exception as e:
                update.message.reply_text(f"\u274c Kanala g\u00f6nderilemedi: {e}")
        else:
            update.message.reply_text("\u26a0\ufe0f Kanal ayarlanmam\u0131\u015f. /setchannel ile ayarlay\u0131n veya admin panelinden 'Video Ekle' kullan\u0131n.")

def handle_channel_post_any(update,context):
    """Kanaldan gelen HER icerik — bundle modu aktifse bundle'a ekle, yoksa video olarak kaydet."""
    msg=update.channel_post
    if not msg:return
    logger.info(f"[KANAL] chat_id={msg.chat_id} type={msg.chat.type} has_video={bool(msg.video)} has_photo={bool(msg.photo)} caption={msg.caption!r}")
    # Bundle modu aktif mi kontrol et
    c.execute("SELECT video_id FROM pending_bundle_uploads WHERE admin_id=?",(ADMIN_ID,))
    brow=c.fetchone()
    if brow:
        vid=brow[0]
        if msg.video:
            fid=msg.video.file_id;btype='video'
        elif msg.photo:
            fid=msg.photo[-1].file_id;btype='photo'
        else:
            return
        c.execute("SELECT MAX(sort_order) FROM video_bundles WHERE video_id=?",(vid,))
        mx=c.fetchone()[0] or 0
        c.execute("INSERT INTO video_bundles (video_id,file_id,file_type,sort_order) VALUES (?,?,?,?)",(vid,fid,btype,mx+1))
        conn.commit()
        c.execute("SELECT COUNT(*) FROM video_bundles WHERE video_id=?",(vid,))
        cnt=c.fetchone()[0]
        ico='\ud83d\udcf8' if btype=='photo' else '\ud83c\udfac'
        try:bot_instance.send_message(chat_id=ADMIN_ID,text=f"{ico} Kanaldan bundle'a eklendi! (#{vid})\nToplam: {cnt} \u00f6\u011fe  —  /donebundle ile bitir")
        except:pass
        return
    # Normal mod: sadece video yakala
    if not msg.video:return
    fid=msg.video.file_id
    tid=msg.video.thumb.file_id if msg.video.thumb else None
    ch=str(msg.chat_id)
    mid=msg.message_id
    title=msg.caption or f"Video #{mid}"
    try:
        c.execute("INSERT INTO videos (category,file_id,title,channel_id,message_id,thumb_file_id) VALUES (?,?,?,?,?,?)",(None,fid,title,ch,mid,tid))
        conn.commit()
        logger.info(f"[KANAL] Video kaydedildi: msg_id={mid} channel={ch} title={title!r}")
        try:bot_instance.send_message(chat_id=ADMIN_ID,text=f"\u2705 Kanal videosu yakalandi!\n\ud83c\udfac {title}\n\ud83d\udcc1 Kategori ver: Admin Panel \u2192 Kanal sekmesi")
        except:pass
    except Exception as e:
        logger.warning(f"[KANAL] Video kayit hatasi: {e}")

class ChannelPostHandler(telegram.ext.Handler):
    """Kanal post'lar\u0131n\u0131 (channel_post) yakalar. PTB 13.x MessageHandler bunu gormez."""
    def __init__(self,callback):
        super().__init__(callback)
    def check_update(self,update):
        return isinstance(update,telegram.Update) and update.channel_post is not None
    def collect_additional_context(self,context,dispatcher,update,check_result):
        return
    def handle_update(self,update,dispatcher,check_result,context=None):
        if context:return self.callback(update,context)
        return self.callback(update,telegram.ext.CallbackContext.from_update(update,dispatcher))

def handle_channel_video(update,context):
    pass  # artık handle_channel_post_any kullaniliyor

def kanaltest_cmd(update,context):
    if update.effective_user.id!=ADMIN_ID:return
    ch_id=get_channel_id()
    if not ch_id:
        update.message.reply_text("\u274c Kanal ayarlanmamis. Admin panelinden Kanal ID girin.")
        return
    try:
        member=bot_instance.get_chat_member(chat_id=ch_id,user_id=bot_instance.get_me().id)
        status=member.status
        if status in('administrator','creator'):
            update.message.reply_text(f"\u2705 Bot kanalda ADMIN!\nKanal: {ch_id}\nDurum: {status}\n\nArtik kanala video yukleyince admin panelinde gorunmeli.")
        else:
            update.message.reply_text(f"\u26a0\ufe0f Bot kanalda var ama ADMIN DEGIL!\nDurum: {status}\n\nKanala git \u2192 Bot'u admin yap \u2192 tekrar dene.")
    except Exception as e:
        update.message.reply_text(f"\u274c Bot kanalda bulunamadi veya hata:\n{e}\n\nKanal ID: {ch_id}\n\nKanala botu admin olarak ekle!")

def video_yukle(update,context):
    if update.effective_user.id!=ADMIN_ID:return update.message.reply_text("\u274c Sadece admin.")
    args=context.args
    if len(args)<2:
        c.execute("SELECT slug FROM categories ORDER BY id")
        return update.message.reply_text("\u274c Kullan\u0131m: /yukle <slug> <Ba\u015fl\u0131k>\n\n"+"\n".join([r[0] for r in c.fetchall()]))
    cat,title=args[0],' '.join(args[1:])
    c.execute("SELECT 1 FROM categories WHERE slug=?",(cat,))
    if not c.fetchone():return update.message.reply_text(f"\u274c '{cat}' bulunamad\u0131.")
    context.user_data['yukle']={'category':cat,'title':title}
    update.message.reply_text(f"\ud83d\udce4 *{title}* videosunu g\u00f6nderin.",parse_mode='Markdown')

def pre_checkout(update,context):
    """Stars odemesini otomatik onayla."""
    query=update.pre_checkout_query
    query.answer(ok=True)
    logger.info(f"PreCheckout onaylandi: user={query.from_user.id} payload={query.invoice_payload}")

def successful_payment(update,context):
    """Odeme basarili — premium otomatik ver."""
    msg=update.message
    uid=msg.from_user.id
    sp=msg.successful_payment
    payload=sp.invoice_payload  # "pkg_{id}_uid_{user_id}"
    stars=sp.total_amount
    # payload'dan paket id'sini coz
    days=30
    pname='Premium'
    try:
        parts=payload.split('_')
        pkg_id=int(parts[1])
        c.execute("SELECT name,days FROM packages WHERE id=?",(pkg_id,))
        row=c.fetchone()
        if row:pname,days=row
    except Exception as e:
        logger.warning(f"Payload parse hatasi: {e} payload={payload}")
    pd=give_premium(uid,days)
    # Referral satın alma bonusu
    try:process_referral_purchase(uid,days)
    except:pass
    # Admin'e bildir
    try:
        bot_instance.send_message(
            ADMIN_ID,
            f"\u2705 *Otomatik Odeme Tamamlandi!*\n\n"
            f"\ud83d\udc64 ID: `{uid}`\n"
            f"\ud83d\udce6 Paket: {pname}\n"
            f"\u2b50 Stars: {stars}\n"
            f"\ud83d\udcc5 Bitis: {pd}",
            parse_mode='Markdown'
        )
    except:pass
    # Kullaniciya bildir
    kb=[[telegram.InlineKeyboardButton(f"\ud83c\udfac {BOT_NAME}'i Ac",web_app=telegram.WebAppInfo(url=WEBAPP_URL))]]
    try:
        bot_instance.send_message(
            uid,
            f"\ud83c\udf89 *Odemeniz Basarili!*\n\n"
            f"\ud83d\udce6 {pname}\n"
            f"\u2b50 {stars} Stars odendi\n"
            f"\ud83d\udcc5 Bitis: {pd}\n\n"
            f"Premium uyeliginiz aktif! Uygulamayi acin.",
            parse_mode='Markdown',
            reply_markup=telegram.InlineKeyboardMarkup(kb)
        )
    except:pass
    logger.info(f"Premium verildi (Stars odeme): user={uid} pkg={pname} days={days} stars={stars}")

def log_every_update(update,context):
    """HER gelen update'i logla (debug)."""
    uid=update.update_id
    if update.channel_post:
        cp=update.channel_post
        logger.info(f"[UPDATE #{uid}] channel_post chat={cp.chat_id} type={cp.chat.type} video={bool(cp.video)}")
    elif update.message:
        m=update.message
        logger.info(f"[UPDATE #{uid}] message chat={m.chat_id} type={m.chat.type} text={m.text!r}")
    else:
        logger.info(f"[UPDATE #{uid}] other type")

def yanit_cmd(update,context):
    """Admin destek yanıtı: /yanit <user_id> <mesaj>"""
    if update.message.from_user.id!=ADMIN_ID:return
    args=context.args
    if len(args)<2:return update.message.reply_text("Kullanım: /yanit <user_id> <mesaj>")
    try:uid=int(args[0])
    except:return update.message.reply_text("Geçersiz user_id")
    reply_text=' '.join(args[1:])
    db,cur=get_db()
    now=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur.execute("UPDATE support_messages SET status='replied',reply_text=?,replied_at=? WHERE user_id=? AND status='open'",(reply_text,now,uid))
    db.commit()
    try:
        kb=[[telegram.InlineKeyboardButton(f"🎬 {BOT_NAME}'i Aç",web_app=telegram.WebAppInfo(url=WEBAPP_URL))]]
        bot_instance.send_message(uid,f"📩 *Destek Yanıtı*\n\n{reply_text}",parse_mode='Markdown',reply_markup=telegram.InlineKeyboardMarkup(kb))
        update.message.reply_text(f"✅ Yanıt gönderildi → {uid}")
    except Exception as e:update.message.reply_text(f"❌ Hata: {e}")

@app.route('/ping')
def ping():
    return jsonify({'ok':True,'status':'alive'})

def keep_alive_loop():
    import time
    port=int(os.environ.get('PORT',5000))
    url=f'http://127.0.0.1:{port}/ping'
    while True:
        time.sleep(270)
        try:
            req_lib.get(url,timeout=10)
            logger.info('[keep-alive] ping OK')
        except Exception as e:
            logger.warning(f'[keep-alive] ping hatasi: {e}')

def run_flask():
    port=int(os.environ.get('PORT',5000))
    app.run(host='0.0.0.0',port=port,debug=False,use_reloader=False)

def main():
    global bot_instance
    threading.Thread(target=run_flask,daemon=True).start()
    logger.info("Flask port 5000'de baslatildi.")
    threading.Thread(target=keep_alive_loop,daemon=True).start()
    logger.info("[keep-alive] thread baslatildi (her 4.5 dakikada ping)")
    updater=Updater(TOKEN,use_context=True)
    bot_instance=updater.bot
    global BOT_USERNAME
    try:BOT_USERNAME=bot_instance.get_me().username or ''
    except:pass
    dp=updater.dispatcher
    from telegram.ext import CallbackQueryHandler
    dp.add_handler(CommandHandler('start',start))
    dp.add_handler(CommandHandler('menu',menu_cmd))
    dp.add_handler(CallbackQueryHandler(handle_callback))
    dp.add_handler(CommandHandler('admin',admin_cmd))
    dp.add_handler(CommandHandler('yukle',video_yukle))
    dp.add_handler(CommandHandler('setchannel',setchannel_cmd))
    dp.add_handler(CommandHandler('kanaltest',kanaltest_cmd))
    dp.add_handler(CommandHandler('bundle',bundle_cmd))
    dp.add_handler(CommandHandler('donebundle',donebundle_cmd))
    dp.add_handler(CommandHandler('yanit',yanit_cmd))
    dp.add_handler(CommandHandler('duyuru',duyuru_cmd))
    dp.add_handler(CommandHandler('duyuruvip',duyuruvip_cmd))
    dp.add_handler(ChannelPostHandler(handle_channel_post_any))
    dp.add_handler(MessageHandler(Filters.photo & Filters.chat_type.private,handle_photo))
    dp.add_handler(MessageHandler(Filters.video & Filters.chat_type.private,handle_video))
    dp.add_handler(MessageHandler(Filters.all,log_every_update),group=3)
    # Stars odeme handler'lari
    from telegram.ext import PreCheckoutQueryHandler
    dp.add_handler(PreCheckoutQueryHandler(pre_checkout))
    dp.add_handler(MessageHandler(Filters.successful_payment,successful_payment))
    # Bot komut listesini ayarla
    try:
        bot_instance.set_my_commands([
            telegram.BotCommand('start','Botu başlat / Ana menü'),
            telegram.BotCommand('menu','📋 Ana menüyü aç'),
            telegram.BotCommand('duyuru','📢 Tüm kullanıcılara duyuru gönder (admin)'),
            telegram.BotCommand('duyuruvip','⭐ Sadece premium üyelere duyuru (admin)'),
        ])
    except Exception as e:
        logger.warning(f"set_my_commands hatası: {e}")
    logger.info(f"{BOT_NAME} baslatiliyor... [polling aktif]")
    updater.start_polling()
    updater.idle()

if __name__=='__main__':main()
