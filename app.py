import os, json, secrets
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, redirect, flash
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3

try:
    from flask_socketio import SocketIO, emit, join_room
    HAS_SOCKETIO = True
except ImportError:
    HAS_SOCKETIO = False

try:
    import stripe
    stripe.api_key = os.environ.get('STRIPE_SECRET_KEY','')
    HAS_STRIPE = bool(stripe.api_key)
except ImportError:
    HAS_STRIPE = False

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'swiftly-stable-secret-key-2024-change-on-render')
app.permanent_session_lifetime = timedelta(days=30)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'swiftly.db')

if HAS_SOCKETIO:
    socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

# ── DB ────────────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        phone TEXT,
        address TEXT,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'customer',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS stores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        manager_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'food',
        description TEXT,
        address TEXT,
        phone TEXT,
        emoji TEXT DEFAULT '🏪',
        image_url TEXT,
        delivery_time TEXT DEFAULT '20-35',
        min_order REAL DEFAULT 0,
        delivery_fee REAL DEFAULT 2.99,
        free_delivery_threshold REAL DEFAULT 0,
        rating REAL DEFAULT 5.0,
        is_open INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT,
        price REAL NOT NULL,
        emoji TEXT DEFAULT '📦',
        image_url TEXT,
        category TEXT DEFAULT 'main',
        dietary_labels TEXT DEFAULT '[]',
        order_count INTEGER DEFAULT 0,
        available INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS drivers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE NOT NULL,
        vehicle TEXT DEFAULT 'Bike',
        license_plate TEXT,
        status TEXT DEFAULT 'offline',
        current_order_id INTEGER,
        earnings REAL DEFAULT 0.0,
        total_deliveries INTEGER DEFAULT 0,
        rating REAL DEFAULT 5.0,
        lat REAL,
        lng REAL
    );
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER,
        customer_name TEXT NOT NULL,
        customer_phone TEXT,
        customer_address TEXT NOT NULL,
        customer_lat REAL,
        customer_lng REAL,
        store_id INTEGER,
        store_name TEXT,
        items_json TEXT NOT NULL,
        subtotal REAL NOT NULL,
        delivery_fee REAL DEFAULT 2.99,
        tip REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        promo_code TEXT,
        total REAL NOT NULL,
        status TEXT DEFAULT 'placed',
        driver_id INTEGER,
        notes TEXT,
        payment_method TEXT DEFAULT 'cash',
        payment_intent_id TEXT,
        scheduled_for TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER UNIQUE,
        customer_id INTEGER,
        customer_name TEXT,
        store_id INTEGER,
        driver_id INTEGER,
        store_rating INTEGER DEFAULT 5,
        driver_rating INTEGER DEFAULT 5,
        comment TEXT DEFAULT '',
        created_at TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now'))
    );
    CREATE TABLE IF NOT EXISTS favourites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        store_id INTEGER NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, store_id)
    );
    CREATE TABLE IF NOT EXISTS saved_addresses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        label TEXT DEFAULT 'Home',
        address TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS promo_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        discount_type TEXT DEFAULT 'percent',
        discount_value REAL NOT NULL,
        min_order REAL DEFAULT 0,
        max_uses INTEGER DEFAULT 100,
        uses_count INTEGER DEFAULT 0,
        expires_at TEXT,
        is_active INTEGER DEFAULT 1,
        store_id INTEGER,
        created_by INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL,
        sender_name TEXT,
        sender_role TEXT NOT NULL,
        message TEXT NOT NULL,
        created_at TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now'))
    );
    CREATE TABLE IF NOT EXISTS loyalty_points (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE NOT NULL,
        points INTEGER DEFAULT 0,
        total_earned INTEGER DEFAULT 0
    );
    """)
    conn.commit()
    # Migrations for existing DBs
    _migrate(conn)
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        _seed(conn)
    conn.close()

def _migrate(conn):
    """Safe column additions for existing databases."""
    migrations = [
        ("orders", "tip", "REAL DEFAULT 0"),
        ("orders", "discount", "REAL DEFAULT 0"),
        ("orders", "promo_code", "TEXT"),
        ("orders", "scheduled_for", "TEXT"),
        ("orders", "payment_intent_id", "TEXT"),
        ("orders", "customer_lat", "REAL"),
        ("orders", "customer_lng", "REAL"),
        ("products", "category", "TEXT DEFAULT 'main'"),
        ("products", "dietary_labels", "TEXT DEFAULT '[]'"),
        ("products", "order_count", "INTEGER DEFAULT 0"),
        ("drivers", "lat", "REAL"),
        ("drivers", "lng", "REAL"),
        ("stores", "free_delivery_threshold", "REAL DEFAULT 0"),
    ]
    for table, col, defn in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
            conn.commit()
        except Exception:
            pass

def _seed(conn):
    pw = generate_password_hash('demo123')
    conn.execute("INSERT INTO users (name,email,phone,address,password_hash,role) VALUES (?,?,?,?,?,?)",
        ('Alex Customer','customer@demo.com','07700111222','10 Baker St, London',pw,'customer'))
    def add_manager(name, email, phone):
        cur = conn.execute("INSERT INTO users (name,email,phone,password_hash,role) VALUES (?,?,?,?,?)",
            (name, email, phone, pw, 'manager'))
        return cur.lastrowid
    m1=add_manager('Paolo Romano','pizza@demo.com','07700333444')
    m2=add_manager('Sarah Chen','pharmacy@demo.com','07700555666')
    m3=add_manager('Raj Patel','grocery@demo.com','07700777888')
    m4=add_manager('Joe Burger','burger@demo.com','07700900333')
    m5=add_manager('Yuki Tanaka','sushi@demo.com','07700900444')
    m6=add_manager('TechDrop','tech@demo.com','07700900555')
    def add_driver(name, email, phone, vehicle, plate):
        cur = conn.execute("INSERT INTO users (name,email,phone,password_hash,role) VALUES (?,?,?,?,?)",
            (name, email, phone, pw, 'driver'))
        uid = cur.lastrowid
        conn.execute("INSERT INTO drivers (user_id,vehicle,license_plate) VALUES (?,?,?)", (uid,vehicle,plate))
        return uid
    add_driver('Marcus Reid','driver@demo.com','07700900111','Motorbike','LN71 XYZ')
    add_driver('Priya Shah','driver2@demo.com','07700900222','Bicycle','N/A')
    def add_store(mid, name, cat, desc, addr, emoji, dtime, dfee, rating, free_thresh=0):
        cur = conn.execute(
            "INSERT INTO stores (manager_id,name,category,description,address,emoji,delivery_time,delivery_fee,rating,free_delivery_threshold) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (mid,name,cat,desc,addr,emoji,dtime,dfee,rating,free_thresh))
        return cur.lastrowid
    s1=add_store(m1,'Pizza Palace','food','Authentic Italian, handmade daily','45 Brick Lane','🍕','20-30',1.99,4.8,20.0)
    s2=add_store(m2,'QuickPharm','medicine','24/7 pharmacy, OTC & prescription','12 High St','💊','15-25',0.99,4.9)
    s3=add_store(m3,'FreshMart','grocery','Fresh groceries in 30 mins','8 Market Rd','🛒','25-40',1.49,4.7,25.0)
    s4=add_store(m4,'Burger Barn','food','Gourmet smash burgers','22 King St','🍔','15-25',1.49,4.6,15.0)
    s5=add_store(m5,'Tokyo Kitchen','food','Fresh sushi & Japanese cuisine','5 Japan St','🍱','25-40',2.49,4.9)
    s6=add_store(m6,'TechDrop','general','Phone accessories & gadgets','1 Silicon St','📱','30-50',2.99,4.5)
    prods=[
        (s1,'Margherita Pizza','Classic tomato & mozzarella',9.99,'🍕','pizza','["vegetarian"]'),
        (s1,'Pepperoni Pizza','Loaded with pepperoni',12.99,'🍕','pizza','[]'),
        (s1,'BBQ Chicken Pizza','Smoky BBQ + grilled chicken',13.99,'🍕','pizza','[]'),
        (s1,'Garlic Bread','Baked with butter & herbs',3.49,'🥖','sides','["vegetarian"]'),
        (s1,'Tiramisu','Authentic Italian dessert',4.99,'🍮','desserts','["vegetarian"]'),
        (s2,'Paracetamol 500mg','Pain & fever relief',3.49,'💊','pain_relief','[]'),
        (s2,'Ibuprofen 200mg','Anti-inflammatory',4.29,'💊','pain_relief','[]'),
        (s2,'Vitamin C 1000mg','Immune support',6.99,'🍊','vitamins','["vegan"]'),
        (s2,'Antihistamine','Allergy relief',5.49,'💊','allergy','[]'),
        (s2,'Antiseptic Cream','Wound care',4.99,'🩹','first_aid','[]'),
        (s3,'Whole Milk 2L','Fresh semi-skimmed',1.89,'🥛','dairy','["vegetarian"]'),
        (s3,'Sourdough Bread','Artisan baked daily',2.49,'🍞','bakery','["vegan"]'),
        (s3,'Free Range Eggs x12','Large free range',3.29,'🥚','dairy','["vegetarian"]'),
        (s3,'Basmati Rice 1kg','Long grain basmati',2.99,'🍚','dry_goods','["vegan","gluten_free"]'),
        (s3,'Chicken Breast 500g','Skinless boneless',4.99,'🍗','meat','["gluten_free"]'),
        (s4,'Classic Smash Burger','Double smash with cheese',8.99,'🍔','burgers','[]'),
        (s4,'Crispy Chicken Burger','Southern fried chicken',7.99,'🍔','burgers','[]'),
        (s4,'Loaded Fries','Cheese sauce & jalapeños',3.99,'🍟','sides','["vegetarian"]'),
        (s4,'Onion Rings','Beer-battered rings',2.49,'🧅','sides','["vegetarian"]'),
        (s4,'Thick Milkshake','Vanilla, choc or strawberry',4.49,'🥤','drinks','["vegetarian"]'),
        (s5,'Salmon Sushi Box (8pc)','Premium salmon nigiri',12.99,'🍣','sushi','["gluten_free"]'),
        (s5,'Mixed Rolls (12pc)','Tuna, salmon & avocado',14.99,'🍱','rolls','[]'),
        (s5,'Chicken Ramen','Rich tonkotsu broth',10.99,'🍜','hot_dishes','[]'),
        (s5,'Gyoza (6pc)','Pan-fried pork dumplings',6.49,'🥟','starters','[]'),
        (s5,'Edamame','Salted steamed soy beans',3.49,'🫘','starters','["vegan","gluten_free"]'),
        (s6,'USB-C Cable 2m','Braided fast charge',9.99,'🔌','cables','[]'),
        (s6,'Phone Stand','Adjustable desk holder',7.49,'📱','accessories','[]'),
        (s6,'AA Batteries x8','Long-lasting alkaline',5.49,'🔋','accessories','[]'),
        (s6,'Screen Protector','Tempered glass, universal',4.99,'📲','accessories','[]'),
        (s6,'Wireless Earbuds','Bluetooth 5.0, 24hr battery',24.99,'🎧','audio','[]'),
    ]
    conn.executemany("INSERT INTO products (store_id,name,description,price,emoji,category,dietary_labels) VALUES (?,?,?,?,?,?,?)", prods)
    # Seed promo codes
    conn.execute("INSERT INTO promo_codes (code,discount_type,discount_value,min_order,max_uses) VALUES (?,?,?,?,?)",
        ('WELCOME10','percent',10,0,1000))
    conn.execute("INSERT INTO promo_codes (code,discount_type,discount_value,min_order,max_uses) VALUES (?,?,?,?,?)",
        ('SAVE5','fixed',5,15,500))
    conn.commit()

# ── AUTH HELPERS ──────────────────────────────────────────────────────────────

def login_required(roles=None):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'uid' not in session:
                return redirect('/login')
            if roles and session.get('role') not in roles:
                return redirect('/login')
            return f(*args, **kwargs)
        return decorated
    return decorator

def me():
    if 'uid' not in session:
        return None
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE id=?", (session['uid'],)).fetchone()
    conn.close()
    return dict(u) if u else None

# ── AUTH ROUTES ───────────────────────────────────────────────────────────────

@app.route('/')
def index():
    role = session.get('role')
    if role == 'customer': return redirect('/home')
    if role == 'manager':  return redirect('/manager')
    if role == 'driver':   return redirect('/driver')
    return redirect('/login')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email','').lower().strip()
        password = request.form.get('password','')
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()
        if not user or not check_password_hash(user['password_hash'], password):
            flash('Wrong email or password.', 'error')
            return redirect('/login')
        session.permanent = True
        session['uid']  = user['id']
        session['role'] = user['role']
        session['name'] = user['name']
        if user['role'] == 'customer': return redirect('/home')
        if user['role'] == 'manager':  return redirect('/manager')
        if user['role'] == 'driver':   return redirect('/driver')
    return render_template('auth/login.html', messages=session.pop('_flashes', []))

@app.route('/signup', methods=['GET','POST'])
def signup():
    if request.method == 'POST':
        role     = request.form.get('role', 'customer')
        name     = request.form.get('name','').strip()
        email    = request.form.get('email','').lower().strip()
        phone    = request.form.get('phone','').strip()
        password = request.form.get('password','')
        confirm  = request.form.get('confirm_password','')
        if not name or not email or not password:
            flash('Please fill all required fields.', 'error')
            return redirect('/signup')
        if password != confirm:
            flash('Passwords do not match.', 'error')
            return redirect('/signup')
        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'error')
            return redirect('/signup')
        pw_hash = generate_password_hash(password)
        conn = get_db()
        try:
            cur = conn.execute("INSERT INTO users (name,email,phone,password_hash,role) VALUES (?,?,?,?,?)",
                (name, email, phone, pw_hash, role))
            uid = cur.lastrowid
            if role == 'manager':
                sname = request.form.get('store_name','My Store').strip()
                scat  = request.form.get('store_category','food')
                saddr = request.form.get('store_address','').strip()
                conn.execute("INSERT INTO stores (manager_id,name,category,address) VALUES (?,?,?,?)",
                    (uid, sname, scat, saddr))
            elif role == 'driver':
                vehicle = request.form.get('vehicle','Bike')
                plate   = request.form.get('license_plate','').strip()
                conn.execute("INSERT INTO drivers (user_id,vehicle,license_plate) VALUES (?,?,?)",
                    (uid, vehicle, plate))
            else:
                address = request.form.get('address','').strip()
                conn.execute("UPDATE users SET address=? WHERE id=?", (address, uid))
            conn.commit()
            conn.close()
            session.permanent = True
            session['uid']  = uid
            session['role'] = role
            session['name'] = name
            if role == 'customer': return redirect('/home')
            if role == 'manager':  return redirect('/manager')
            if role == 'driver':   return redirect('/driver')
        except Exception as e:
            conn.close()
            if 'UNIQUE' in str(e):
                flash('An account with that email already exists.', 'error')
            else:
                flash('Something went wrong. Try again.', 'error')
            return redirect('/signup')
    return render_template('auth/signup.html', messages=session.pop('_flashes', []))

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# ── PAGE ROUTES ───────────────────────────────────────────────────────────────

@app.route('/home')
@login_required(roles=['customer'])
def customer_home():
    return render_template('customer/home.html', user=me(),
        stripe_pk=os.environ.get('STRIPE_PUBLISHABLE_KEY',''))

@app.route('/manager')
@login_required(roles=['manager'])
def manager_dashboard():
    conn = get_db()
    store = conn.execute("SELECT * FROM stores WHERE manager_id=?", (session['uid'],)).fetchone()
    conn.close()
    return render_template('manager/dashboard.html', user=me(), store=dict(store) if store else None)

@app.route('/manager/products')
@login_required(roles=['manager'])
def manager_products():
    conn = get_db()
    store = conn.execute("SELECT * FROM stores WHERE manager_id=?", (session['uid'],)).fetchone()
    if not store:
        return redirect('/manager')
    products = conn.execute("SELECT * FROM products WHERE store_id=? ORDER BY id DESC", (store['id'],)).fetchall()
    conn.close()
    return render_template('manager/products.html', user=me(), store=dict(store),
                           products=[dict(p) for p in products])

@app.route('/driver')
@login_required(roles=['driver'])
def driver_dashboard():
    conn = get_db()
    drv = conn.execute("SELECT * FROM drivers WHERE user_id=?", (session['uid'],)).fetchone()
    conn.close()
    return render_template('driver/dashboard.html', user=me(), driver=dict(drv) if drv else {})

@app.route('/profile')
@login_required(roles=['customer'])
def profile_page():
    return render_template('customer/profile.html', user=me())

# ── API: STORES ───────────────────────────────────────────────────────────────

@app.route('/api/stores')
def api_stores():
    cat = request.args.get('category','').strip()
    q   = request.args.get('q','').lower().strip()
    conn = get_db()
    if cat and cat != 'all':
        rows = conn.execute("SELECT * FROM stores WHERE category=? AND is_open=1 ORDER BY rating DESC", (cat,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM stores WHERE is_open=1 ORDER BY rating DESC").fetchall()
    conn.close()
    stores = [dict(r) for r in rows]
    if q:
        stores = [s for s in stores if q in s['name'].lower() or q in (s['description'] or '').lower()]
    # Add favourite flag if logged in
    if 'uid' in session:
        conn = get_db()
        favs = set(r['store_id'] for r in conn.execute("SELECT store_id FROM favourites WHERE user_id=?", (session['uid'],)).fetchall())
        conn.close()
        for s in stores:
            s['is_favourite'] = s['id'] in favs
    return jsonify(stores)

@app.route('/api/stores/<int:sid>/products')
def api_store_products(sid):
    conn = get_db()
    rows = conn.execute("SELECT * FROM products WHERE store_id=? AND available=1 ORDER BY order_count DESC, id", (sid,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/stores/<int:sid>', methods=['PATCH'])
@login_required(roles=['manager'])
def api_update_store(sid):
    conn = get_db()
    store = conn.execute("SELECT id FROM stores WHERE id=? AND manager_id=?", (sid, session['uid'])).fetchone()
    if not store:
        conn.close()
        return jsonify({'error': 'Forbidden'}), 403
    data    = request.json or {}
    allowed = ['name','category','description','address','phone','emoji','image_url',
               'delivery_time','min_order','delivery_fee','free_delivery_threshold','is_open']
    pairs   = [(k, v) for k, v in data.items() if k in allowed]
    if pairs:
        sets = ', '.join(f"{k}=?" for k, _ in pairs)
        vals = [v for _, v in pairs] + [sid]
        conn.execute(f"UPDATE stores SET {sets} WHERE id=?", vals)
        conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── API: PRODUCTS ─────────────────────────────────────────────────────────────

@app.route('/api/products', methods=['POST'])
@login_required(roles=['manager'])
def api_add_product():
    data = request.json or {}
    conn = get_db()
    store = conn.execute("SELECT id FROM stores WHERE manager_id=?", (session['uid'],)).fetchone()
    if not store:
        conn.close()
        return jsonify({'error': 'No store'}), 400
    avail = 1 if data.get('is_available', data.get('available', True)) else 0
    dietary = json.dumps(data.get('dietary_labels', []))
    cur = conn.execute(
        "INSERT INTO products (store_id,name,description,price,emoji,image_url,available,category,dietary_labels) VALUES (?,?,?,?,?,?,?,?,?)",
        (store['id'], data.get('name',''), data.get('description',''),
         float(data.get('price',0)), data.get('emoji','📦'), data.get('image_url',''),
         avail, data.get('category','main'), dietary))
    pid = cur.lastrowid
    conn.commit()
    product = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    conn.close()
    return jsonify(dict(product)), 201

@app.route('/api/products/<int:pid>', methods=['PATCH','DELETE'])
@login_required(roles=['manager'])
def api_product(pid):
    conn = get_db()
    store = conn.execute("SELECT id FROM stores WHERE manager_id=?", (session['uid'],)).fetchone()
    if not store:
        conn.close()
        return jsonify({'error': 'No store'}), 400
    prod = conn.execute("SELECT * FROM products WHERE id=? AND store_id=?", (pid, store['id'])).fetchone()
    if not prod:
        conn.close()
        return jsonify({'error': 'Not found'}), 404
    if request.method == 'DELETE':
        conn.execute("DELETE FROM products WHERE id=?", (pid,))
    else:
        data = request.json or {}
        if 'is_available' in data:
            data['available'] = 1 if data.pop('is_available') else 0
        if 'dietary_labels' in data:
            data['dietary_labels'] = json.dumps(data['dietary_labels'])
        allowed = ['name','description','price','emoji','image_url','available','category','dietary_labels']
        pairs   = [(k, v) for k, v in data.items() if k in allowed]
        if pairs:
            sets = ', '.join(f"{k}=?" for k, _ in pairs)
            vals = [v for _, v in pairs] + [pid]
            conn.execute(f"UPDATE products SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── API: ORDERS ───────────────────────────────────────────────────────────────

@app.route('/api/orders', methods=['GET'])
def api_orders():
    if 'uid' not in session:
        return jsonify([])
    role = session['role']
    uid  = session['uid']
    conn = get_db()
    if role == 'customer':
        rows = conn.execute(
            "SELECT o.*, u.name as driver_name, u.phone as driver_phone "
            "FROM orders o LEFT JOIN drivers drv ON o.driver_id=drv.id "
            "LEFT JOIN users u ON drv.user_id=u.id "
            "WHERE o.customer_id=? ORDER BY o.created_at DESC", (uid,)).fetchall()
    elif role == 'manager':
        store = conn.execute("SELECT id FROM stores WHERE manager_id=?", (uid,)).fetchone()
        if not store:
            conn.close()
            return jsonify([])
        status = request.args.get('status','')
        q = "SELECT o.*, u.name as driver_name FROM orders o LEFT JOIN drivers drv ON o.driver_id=drv.id LEFT JOIN users u ON drv.user_id=u.id WHERE o.store_id=?"
        params = [store['id']]
        if status and status != 'all':
            q += " AND o.status=?"
            params.append(status)
        q += " ORDER BY o.created_at DESC"
        rows = conn.execute(q, params).fetchall()
    elif role == 'driver':
        drv = conn.execute("SELECT id FROM drivers WHERE user_id=?", (uid,)).fetchone()
        if not drv:
            conn.close()
            return jsonify([])
        rows = conn.execute(
            "SELECT * FROM orders WHERE driver_id=? AND status NOT IN ('delivered','cancelled') ORDER BY created_at DESC",
            (drv['id'],)).fetchall()
    else:
        conn.close()
        return jsonify([])
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d['address'] = d.get('customer_address', '')
        result.append(d)
    return jsonify(result)

@app.route('/api/orders', methods=['POST'])
@login_required(roles=['customer'])
def api_create_order():
    data  = request.json or {}
    items = data.get('items', [])
    sid   = data.get('store_id')
    conn  = get_db()
    store = conn.execute("SELECT * FROM stores WHERE id=?", (sid,)).fetchone()
    dfee  = store['delivery_fee'] if store else 2.99
    free_thresh = store['free_delivery_threshold'] if store else 0
    sname = store['name'] if store else 'Unknown'
    user  = conn.execute("SELECT * FROM users WHERE id=?", (session['uid'],)).fetchone()
    sub   = round(sum(i['price'] * i['qty'] for i in items), 2)
    # Free delivery check
    if free_thresh and sub >= free_thresh:
        dfee = 0
    tip     = round(float(data.get('tip', 0)), 2)
    discount = 0
    promo_code = data.get('promo_code','').strip().upper()
    if promo_code:
        pc = conn.execute("SELECT * FROM promo_codes WHERE code=? AND is_active=1 AND (max_uses=0 OR uses_count<max_uses)",
                          (promo_code,)).fetchone()
        if pc and sub >= pc['min_order']:
            if pc['discount_type'] == 'percent':
                discount = round(sub * pc['discount_value'] / 100, 2)
            else:
                discount = min(float(pc['discount_value']), sub)
            conn.execute("UPDATE promo_codes SET uses_count=uses_count+1 WHERE id=?", (pc['id'],))
        else:
            conn.close()
            return jsonify({'error': 'Invalid or expired promo code'}), 400
    total = round(sub + dfee + tip - discount, 2)
    addr  = data.get('address') or user['address'] or ''
    pay_mtd = data.get('payment_method', 'cash')
    sched = data.get('scheduled_for','')
    cur   = conn.execute(
        "INSERT INTO orders (customer_id,customer_name,customer_phone,customer_address,"
        "store_id,store_name,items_json,subtotal,delivery_fee,tip,discount,promo_code,total,notes,payment_method,scheduled_for) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (session['uid'], user['name'], user['phone'], addr,
         sid, sname, json.dumps(items), sub, dfee, tip, discount, promo_code or None,
         total, data.get('notes',''), pay_mtd, sched or None))
    oid = cur.lastrowid
    # Update product order counts
    for item in items:
        conn.execute("UPDATE products SET order_count=order_count+? WHERE id=?", (item.get('qty',1), item.get('id')))
    # Award loyalty points (1 point per £1 spent)
    points = int(total)
    conn.execute("INSERT INTO loyalty_points (user_id,points,total_earned) VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET points=points+?,total_earned=total_earned+?",
                 (session['uid'], points, points, points, points))
    conn.commit()
    # Emit socket event
    if HAS_SOCKETIO:
        try:
            socketio.emit('new_order', {'order_id': oid, 'store_id': sid}, room=f'store_{sid}')
        except Exception:
            pass
    conn.close()
    return jsonify({'ok': True, 'order_id': oid, 'total': total, 'discount': discount}), 201

@app.route('/api/orders/<int:oid>')
def api_order(oid):
    conn = get_db()
    row  = conn.execute(
        "SELECT o.*, u.name as driver_name, u.phone as driver_phone, "
        "drv.lat as driver_lat, drv.lng as driver_lng "
        "FROM orders o LEFT JOIN drivers drv ON o.driver_id=drv.id "
        "LEFT JOIN users u ON drv.user_id=u.id WHERE o.id=?", (oid,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(dict(row))

@app.route('/api/orders/<int:oid>/status', methods=['PATCH'])
def api_order_status(oid):
    if 'uid' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data       = request.json or {}
    new_status = data.get('status')
    driver_id  = data.get('driver_id')
    now        = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    conn       = get_db()
    if driver_id:
        conn.execute("UPDATE orders SET status=?,driver_id=?,updated_at=? WHERE id=?",
                     (new_status, driver_id, now, oid))
        conn.execute("UPDATE drivers SET status='busy',current_order_id=? WHERE id=?", (oid, driver_id))
    else:
        conn.execute("UPDATE orders SET status=?,updated_at=? WHERE id=?", (new_status, now, oid))
    if new_status in ('delivered','cancelled'):
        conn.execute("UPDATE drivers SET status='available',current_order_id=NULL WHERE current_order_id=?", (oid,))
        if new_status == 'delivered':
            row = conn.execute("SELECT driver_id FROM orders WHERE id=?", (oid,)).fetchone()
            if row and row['driver_id']:
                conn.execute("UPDATE drivers SET earnings=earnings+3.0,total_deliveries=total_deliveries+1 WHERE id=?",
                             (row['driver_id'],))
    conn.commit()
    # Emit socket event
    if HAS_SOCKETIO:
        try:
            order = conn.execute("SELECT customer_id, store_id FROM orders WHERE id=?", (oid,)).fetchone()
            if order:
                socketio.emit('order_update', {'order_id': oid, 'status': new_status},
                              room=f'order_{oid}')
        except Exception:
            pass
    conn.close()
    return jsonify({'ok': True})

# ── API: FAVOURITES ───────────────────────────────────────────────────────────

@app.route('/api/favourites', methods=['GET'])
@login_required(roles=['customer'])
def api_get_favourites():
    conn = get_db()
    rows = conn.execute(
        "SELECT s.* FROM stores s JOIN favourites f ON s.id=f.store_id WHERE f.user_id=? ORDER BY f.created_at DESC",
        (session['uid'],)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/favourites/<int:sid>', methods=['POST','DELETE'])
@login_required(roles=['customer'])
def api_toggle_favourite(sid):
    conn = get_db()
    if request.method == 'POST':
        try:
            conn.execute("INSERT INTO favourites (user_id,store_id) VALUES (?,?)", (session['uid'], sid))
            conn.commit()
        except Exception:
            pass
        conn.close()
        return jsonify({'ok': True, 'favourited': True})
    else:
        conn.execute("DELETE FROM favourites WHERE user_id=? AND store_id=?", (session['uid'], sid))
        conn.commit()
        conn.close()
        return jsonify({'ok': True, 'favourited': False})

# ── API: SAVED ADDRESSES ──────────────────────────────────────────────────────

@app.route('/api/addresses', methods=['GET','POST'])
@login_required(roles=['customer'])
def api_addresses():
    conn = get_db()
    if request.method == 'GET':
        rows = conn.execute("SELECT * FROM saved_addresses WHERE user_id=? ORDER BY created_at DESC",
                            (session['uid'],)).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    data = request.json or {}
    conn.execute("INSERT INTO saved_addresses (user_id,label,address) VALUES (?,?,?)",
                 (session['uid'], data.get('label','Home'), data.get('address','')))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/addresses/<int:aid>', methods=['DELETE'])
@login_required(roles=['customer'])
def api_delete_address(aid):
    conn = get_db()
    conn.execute("DELETE FROM saved_addresses WHERE id=? AND user_id=?", (aid, session['uid']))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── API: PROMO CODES ──────────────────────────────────────────────────────────

@app.route('/api/promo/validate', methods=['POST'])
def api_validate_promo():
    data = request.json or {}
    code = data.get('code','').strip().upper()
    total = float(data.get('total', 0))
    conn = get_db()
    pc = conn.execute("SELECT * FROM promo_codes WHERE code=? AND is_active=1 AND (max_uses=0 OR uses_count<max_uses)",
                      (code,)).fetchone()
    conn.close()
    if not pc:
        return jsonify({'valid': False, 'error': 'Invalid or expired code'})
    if total < pc['min_order']:
        return jsonify({'valid': False, 'error': f'Minimum order £{pc["min_order"]:.2f} required'})
    if pc['discount_type'] == 'percent':
        discount = round(total * pc['discount_value'] / 100, 2)
        label = f"{int(pc['discount_value'])}% off"
    else:
        discount = min(float(pc['discount_value']), total)
        label = f"£{discount:.2f} off"
    return jsonify({'valid': True, 'discount': discount, 'label': label})

@app.route('/api/promo', methods=['GET','POST'])
@login_required(roles=['manager'])
def api_promo():
    conn = get_db()
    store = conn.execute("SELECT id FROM stores WHERE manager_id=?", (session['uid'],)).fetchone()
    if request.method == 'GET':
        rows = conn.execute("SELECT * FROM promo_codes WHERE (created_by IS NULL OR created_by=? OR store_id=?) ORDER BY created_at DESC",
                            (session['uid'], store['id'] if store else -1)).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    data = request.json or {}
    conn.execute("INSERT INTO promo_codes (code,discount_type,discount_value,min_order,max_uses,store_id,created_by) VALUES (?,?,?,?,?,?,?)",
                 (data.get('code','').upper(), data.get('discount_type','percent'),
                  float(data.get('discount_value',10)), float(data.get('min_order',0)),
                  int(data.get('max_uses',100)), store['id'] if store else None, session['uid']))
    conn.commit()
    conn.close()
    return jsonify({'ok': True}), 201

@app.route('/api/promo/<int:pid>', methods=['DELETE'])
@login_required(roles=['manager'])
def api_delete_promo(pid):
    conn = get_db()
    conn.execute("UPDATE promo_codes SET is_active=0 WHERE id=? AND created_by=?", (pid, session['uid']))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── API: CHAT ─────────────────────────────────────────────────────────────────

@app.route('/api/chat/<int:oid>', methods=['GET','POST'])
def api_chat(oid):
    if 'uid' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db()
    if request.method == 'GET':
        rows = conn.execute("SELECT * FROM chat_messages WHERE order_id=? ORDER BY created_at ASC", (oid,)).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    data = request.json or {}
    msg = data.get('message','').strip()
    if not msg:
        conn.close()
        return jsonify({'error': 'Empty message'}), 400
    conn.execute("INSERT INTO chat_messages (order_id,sender_id,sender_name,sender_role,message) VALUES (?,?,?,?,?)",
                 (oid, session['uid'], session.get('name',''), session.get('role',''), msg))
    conn.commit()
    if HAS_SOCKETIO:
        try:
            socketio.emit('chat_message', {'order_id': oid, 'sender': session.get('name',''), 'message': msg},
                          room=f'order_{oid}')
        except Exception:
            pass
    conn.close()
    return jsonify({'ok': True})

# ── API: PROFILE ──────────────────────────────────────────────────────────────

@app.route('/api/profile', methods=['GET','PATCH'])
@login_required()
def api_profile():
    conn = get_db()
    if request.method == 'GET':
        u = conn.execute("SELECT id,name,email,phone,address,role FROM users WHERE id=?", (session['uid'],)).fetchone()
        pts = conn.execute("SELECT points,total_earned FROM loyalty_points WHERE user_id=?", (session['uid'],)).fetchone()
        conn.close()
        result = dict(u) if u else {}
        result['loyalty_points'] = pts['points'] if pts else 0
        result['total_earned_points'] = pts['total_earned'] if pts else 0
        return jsonify(result)
    data = request.json or {}
    allowed = ['name','phone','address']
    pairs = [(k, v) for k, v in data.items() if k in allowed]
    if 'new_password' in data and data['new_password']:
        u = conn.execute("SELECT password_hash FROM users WHERE id=?", (session['uid'],)).fetchone()
        if not check_password_hash(u['password_hash'], data.get('current_password','')):
            conn.close()
            return jsonify({'error': 'Current password incorrect'}), 400
        pairs.append(('password_hash', generate_password_hash(data['new_password'])))
    if pairs:
        sets = ', '.join(f"{k}=?" for k, _ in pairs)
        vals = [v for _, v in pairs] + [session['uid']]
        conn.execute(f"UPDATE users SET {sets} WHERE id=?", vals)
        conn.commit()
        if 'name' in dict(pairs):
            session['name'] = dict(pairs).get('name', session['name'])
    conn.close()
    return jsonify({'ok': True})

# ── API: DRIVERS ──────────────────────────────────────────────────────────────

@app.route('/api/drivers')
@login_required(roles=['manager'])
def api_drivers():
    status = request.args.get('status','')
    conn = get_db()
    q = "SELECT d.*,u.name,u.phone FROM drivers d JOIN users u ON d.user_id=u.id"
    params = []
    if status:
        q += " WHERE d.status=?"
        params.append(status)
    rows = conn.execute(q, params).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d['is_online'] = d.get('status') not in ('offline', None)
        result.append(d)
    return jsonify(result)

@app.route('/api/drivers/me', methods=['GET','PATCH'])
@login_required(roles=['driver'])
def api_driver_me():
    conn = get_db()
    drv = conn.execute("SELECT d.*, u.name, u.phone FROM drivers d JOIN users u ON d.user_id=u.id WHERE d.user_id=?",
                       (session['uid'],)).fetchone()
    if not drv:
        conn.close()
        return jsonify({'error': 'No driver profile'}), 404
    if request.method == 'PATCH':
        data = request.json or {}
        if 'is_online' in data:
            new_status = 'available' if data['is_online'] else 'offline'
            conn.execute("UPDATE drivers SET status=? WHERE id=?", (new_status, drv['id']))
            conn.commit()
        elif 'status' in data:
            conn.execute("UPDATE drivers SET status=? WHERE id=?", (data['status'], drv['id']))
            conn.commit()
        drv = conn.execute("SELECT d.*, u.name, u.phone FROM drivers d JOIN users u ON d.user_id=u.id WHERE d.user_id=?",
                           (session['uid'],)).fetchone()
    conn.close()
    result = dict(drv)
    result['is_online'] = result.get('status') not in ('offline', None)
    return jsonify(result)

@app.route('/api/drivers/me/location', methods=['PATCH'])
@login_required(roles=['driver'])
def api_driver_location():
    data = request.json or {}
    lat = data.get('lat')
    lng = data.get('lng')
    conn = get_db()
    drv = conn.execute("SELECT id FROM drivers WHERE user_id=?", (session['uid'],)).fetchone()
    if drv and lat is not None and lng is not None:
        conn.execute("UPDATE drivers SET lat=?,lng=? WHERE id=?", (lat, lng, drv['id']))
        conn.commit()
        if HAS_SOCKETIO:
            order = conn.execute("SELECT id FROM orders WHERE driver_id=? AND status='picked_up'", (drv['id'],)).fetchone()
            if order:
                try:
                    socketio.emit('driver_location', {'lat': lat, 'lng': lng}, room=f'order_{order["id"]}')
                except Exception:
                    pass
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/drivers/me/orders')
@login_required(roles=['driver'])
def api_driver_orders():
    conn = get_db()
    drv = conn.execute("SELECT id FROM drivers WHERE user_id=?", (session['uid'],)).fetchone()
    if not drv:
        conn.close()
        return jsonify([])
    rows = conn.execute(
        "SELECT o.*, s.name as store_name FROM orders o LEFT JOIN stores s ON o.store_id=s.id "
        "WHERE o.driver_id=? ORDER BY o.created_at DESC LIMIT 100", (drv['id'],)).fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        d['address'] = d.get('customer_address', '')
        results.append(d)
    return jsonify(results)

@app.route('/api/drivers/me/earnings')
@login_required(roles=['driver'])
def api_driver_earnings():
    conn = get_db()
    drv = conn.execute("SELECT id FROM drivers WHERE user_id=?", (session['uid'],)).fetchone()
    if not drv:
        conn.close()
        return jsonify([])
    rows = conn.execute(
        "SELECT DATE(created_at) as date, COUNT(*) as deliveries, SUM(3.0) as earnings "
        "FROM orders WHERE driver_id=? AND status='delivered' GROUP BY DATE(created_at) ORDER BY date DESC LIMIT 30",
        (drv['id'],)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/drivers/pending-orders')
@login_required(roles=['driver'])
def api_driver_pending_orders():
    conn = get_db()
    rows = conn.execute(
        "SELECT o.*, s.name as store_name FROM orders o LEFT JOIN stores s ON o.store_id=s.id "
        "WHERE o.status IN ('accepted','preparing') AND o.driver_id IS NULL ORDER BY o.created_at ASC").fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        d['address'] = d.get('customer_address', '')
        results.append(d)
    return jsonify(results)

@app.route('/api/orders/<int:oid>/accept', methods=['POST'])
@login_required(roles=['driver'])
def api_driver_accept_order(oid):
    conn = get_db()
    drv = conn.execute("SELECT id FROM drivers WHERE user_id=?", (session['uid'],)).fetchone()
    if not drv:
        conn.close()
        return jsonify({'error': 'No driver profile'}), 404
    order = conn.execute("SELECT id,status,driver_id FROM orders WHERE id=?", (oid,)).fetchone()
    if not order:
        conn.close()
        return jsonify({'error': 'Order not found'}), 404
    if order['driver_id']:
        conn.close()
        return jsonify({'error': 'Already claimed'}), 409
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute("UPDATE orders SET driver_id=?,status='picked_up',updated_at=? WHERE id=?", (drv['id'], now, oid))
    conn.execute("UPDATE drivers SET status='busy',current_order_id=? WHERE id=?", (oid, drv['id']))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'order_id': oid})

@app.route('/api/orders/<int:oid>/decline', methods=['POST'])
@login_required(roles=['driver'])
def api_driver_decline_order(oid):
    return jsonify({'ok': True})

# ── API: REVIEWS ──────────────────────────────────────────────────────────────

@app.route('/api/orders/<int:oid>/review', methods=['POST'])
@login_required(roles=['customer'])
def api_review_order(oid):
    data = request.json or {}
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id=? AND customer_id=?", (oid, session['uid'])).fetchone()
    if not order:
        conn.close()
        return jsonify({'error': 'Order not found'}), 404
    user = conn.execute("SELECT name FROM users WHERE id=?", (session['uid'],)).fetchone()
    cname = user['name'] if user else 'Customer'
    conn.execute("""INSERT OR REPLACE INTO reviews
        (order_id,customer_id,customer_name,store_id,driver_id,store_rating,driver_rating,comment)
        VALUES (?,?,?,?,?,?,?,?)""",
        (oid, session['uid'], cname, order['store_id'], order['driver_id'],
         int(data.get('store_rating', 5)), int(data.get('driver_rating', 5)), data.get('comment', '')))
    conn.execute("UPDATE stores SET rating=(SELECT AVG(CAST(store_rating AS REAL)) FROM reviews WHERE store_id=?) WHERE id=?",
                 (order['store_id'], order['store_id']))
    if order['driver_id']:
        conn.execute("UPDATE drivers SET rating=(SELECT AVG(CAST(driver_rating AS REAL)) FROM reviews WHERE driver_id=?) WHERE id=?",
                     (order['driver_id'], order['driver_id']))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/reviews')
@login_required(roles=['manager'])
def api_reviews():
    conn = get_db()
    store = conn.execute("SELECT id FROM stores WHERE manager_id=?", (session['uid'],)).fetchone()
    if not store:
        conn.close()
        return jsonify([])
    rows = conn.execute("""SELECT r.*, o.total, o.items_json, o.created_at as order_date
        FROM reviews r JOIN orders o ON r.order_id=o.id WHERE r.store_id=? ORDER BY r.created_at DESC""",
        (store['id'],)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/driver/reviews')
@login_required(roles=['driver'])
def api_driver_reviews():
    conn = get_db()
    drv = conn.execute("SELECT id FROM drivers WHERE user_id=?", (session['uid'],)).fetchone()
    if not drv:
        conn.close()
        return jsonify([])
    rows = conn.execute("""SELECT r.*, o.store_name, o.created_at as order_date
        FROM reviews r JOIN orders o ON r.order_id=o.id WHERE r.driver_id=? ORDER BY r.created_at DESC""",
        (drv['id'],)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

# ── API: STATS & ANALYTICS ────────────────────────────────────────────────────

@app.route('/api/stats')
@login_required(roles=['manager'])
def api_stats():
    conn = get_db()
    store = conn.execute("SELECT id FROM stores WHERE manager_id=?", (session['uid'],)).fetchone()
    if not store:
        conn.close()
        return jsonify({'revenue':0,'pending':0,'total':0,'delivered':0})
    sid = store['id']
    today = datetime.utcnow().strftime('%Y-%m-%d')
    revenue = conn.execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE store_id=? AND status='delivered' AND created_at LIKE ?",
                           (sid, today+'%')).fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM orders WHERE store_id=? AND status NOT IN ('delivered','cancelled')", (sid,)).fetchone()[0]
    total   = conn.execute("SELECT COUNT(*) FROM orders WHERE store_id=?", (sid,)).fetchone()[0]
    delivered = conn.execute("SELECT COUNT(*) FROM orders WHERE store_id=? AND status='delivered'", (sid,)).fetchone()[0]
    conn.close()
    return jsonify({'revenue': revenue, 'pending': pending, 'total': total, 'delivered': delivered})

@app.route('/api/analytics')
@login_required(roles=['manager'])
def api_analytics():
    conn = get_db()
    store = conn.execute("SELECT id FROM stores WHERE manager_id=?", (session['uid'],)).fetchone()
    if not store:
        conn.close()
        return jsonify({})
    sid = store['id']
    # Revenue by day (last 7 days)
    daily = conn.execute("""SELECT DATE(created_at) as date, COUNT(*) as orders, COALESCE(SUM(total),0) as revenue
        FROM orders WHERE store_id=? AND status='delivered' AND created_at >= date('now','-7 days')
        GROUP BY DATE(created_at) ORDER BY date ASC""", (sid,)).fetchall()
    # Best selling items
    best = conn.execute("""SELECT p.name, p.emoji, SUM(p.order_count) as cnt
        FROM products p WHERE p.store_id=? ORDER BY p.order_count DESC LIMIT 5""", (sid,)).fetchall()
    # Revenue by hour
    hourly = conn.execute("""SELECT strftime('%H',created_at) as hour, COUNT(*) as orders
        FROM orders WHERE store_id=? AND status='delivered' GROUP BY hour ORDER BY hour""", (sid,)).fetchall()
    # Payment method breakdown
    payments = conn.execute("""SELECT payment_method, COUNT(*) as cnt FROM orders WHERE store_id=? GROUP BY payment_method""", (sid,)).fetchall()
    conn.close()
    return jsonify({
        'daily': [dict(r) for r in daily],
        'best_sellers': [dict(r) for r in best],
        'hourly': [dict(r) for r in hourly],
        'payments': [dict(r) for r in payments],
    })

# ── API: STRIPE ───────────────────────────────────────────────────────────────

@app.route('/api/payment/intent', methods=['POST'])
@login_required(roles=['customer'])
def api_payment_intent():
    if not HAS_STRIPE:
        return jsonify({'error': 'Stripe not configured'}), 503
    data = request.json or {}
    amount = int(float(data.get('amount', 0)) * 100)  # pence
    try:
        intent = stripe.PaymentIntent.create(
            amount=amount,
            currency='gbp',
            metadata={'user_id': session['uid']},
        )
        return jsonify({'client_secret': intent.client_secret, 'publishable_key': os.environ.get('STRIPE_PUBLISHABLE_KEY','')})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── SOCKETIO EVENTS ───────────────────────────────────────────────────────────

if HAS_SOCKETIO:
    @socketio.on('join')
    def on_join(data):
        room = data.get('room')
        if room:
            join_room(room)

# ── PWA ───────────────────────────────────────────────────────────────────────

@app.route('/manifest.json')
def manifest():
    return jsonify({
        "name": "Swiftly Delivery",
        "short_name": "Swiftly",
        "description": "Fast food, grocery & medicine delivery",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#06c167",
        "icons": [
            {"src": "https://via.placeholder.com/192x192/06c167/ffffff?text=S", "sizes": "192x192", "type": "image/png"},
            {"src": "https://via.placeholder.com/512x512/06c167/ffffff?text=S", "sizes": "512x512", "type": "image/png"}
        ]
    })

@app.route('/sw.js')
def service_worker():
    js = """
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => clients.claim());
self.addEventListener('fetch', e => e.respondWith(fetch(e.request).catch(() => caches.match(e.request))));
"""
    from flask import Response
    return Response(js, mimetype='application/javascript')

init_db()

if __name__ == '__main__':
    if HAS_SOCKETIO:
        socketio.run(app, debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
    else:
        app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
