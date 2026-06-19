import os, json, secrets
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, redirect, flash
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.permanent_session_lifetime = timedelta(days=30)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'swiftly.db')

# ── DB ────────────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def _check_schema():
    """Delete DB file if schema is outdated, so it's recreated fresh."""
    if not os.path.exists(DB_PATH):
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        info = conn.execute("PRAGMA table_info(drivers)").fetchall()
        cols = [r[1] for r in info]
        info2 = conn.execute("PRAGMA table_info(orders)").fetchall()
        cols2 = [r[1] for r in info2]
        conn.close()
        if 'user_id' not in cols or 'customer_address' not in cols2:
            print("Detected old database schema — deleting swiftly.db to recreate fresh...")
            os.remove(DB_PATH)
            return
        # Migrate: add payment_method if missing
        if 'payment_method' not in cols2:
            try:
                conn2 = sqlite3.connect(DB_PATH)
                conn2.execute("ALTER TABLE orders ADD COLUMN payment_method TEXT DEFAULT 'cash'")
                conn2.commit()
                conn2.close()
            except Exception:
                pass
    except Exception:
        pass  # If anything goes wrong, let init_db handle it

def init_db():
    _check_schema()
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
        rating REAL DEFAULT 5.0
    );
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER,
        customer_name TEXT NOT NULL,
        customer_phone TEXT,
        customer_address TEXT NOT NULL,
        store_id INTEGER,
        store_name TEXT,
        items_json TEXT NOT NULL,
        subtotal REAL NOT NULL,
        delivery_fee REAL DEFAULT 2.99,
        total REAL NOT NULL,
        status TEXT DEFAULT 'placed',
        driver_id INTEGER,
        notes TEXT,
        payment_method TEXT DEFAULT 'cash',
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
    """)
    conn.commit()
    # Seed demo data only if empty
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        _seed(conn)
    conn.close()

def _seed(conn):
    pw = generate_password_hash('demo123')

    # Customers
    conn.execute("INSERT INTO users (name,email,phone,address,password_hash,role) VALUES (?,?,?,?,?,?)",
        ('Alex Customer','customer@demo.com','07700111222','10 Baker St, London',pw,'customer'))

    # Managers
    def add_manager(name, email, phone):
        cur = conn.execute("INSERT INTO users (name,email,phone,password_hash,role) VALUES (?,?,?,?,?)",
            (name, email, phone, pw, 'manager'))
        return cur.lastrowid

    m1 = add_manager('Paolo Romano','pizza@demo.com','07700333444')
    m2 = add_manager('Sarah Chen','pharmacy@demo.com','07700555666')
    m3 = add_manager('Raj Patel','grocery@demo.com','07700777888')
    m4 = add_manager('Joe Burger','burger@demo.com','07700900333')
    m5 = add_manager('Yuki Tanaka','sushi@demo.com','07700900444')
    m6 = add_manager('TechDrop','tech@demo.com','07700900555')

    # Drivers
    def add_driver(name, email, phone, vehicle, plate):
        cur = conn.execute("INSERT INTO users (name,email,phone,password_hash,role) VALUES (?,?,?,?,?)",
            (name, email, phone, pw, 'driver'))
        uid = cur.lastrowid
        conn.execute("INSERT INTO drivers (user_id,vehicle,license_plate) VALUES (?,?,?)", (uid,vehicle,plate))
        return uid

    add_driver('Marcus Reid','driver@demo.com','07700900111','Motorbike','LN71 XYZ')
    add_driver('Priya Shah','driver2@demo.com','07700900222','Bicycle','N/A')

    # Stores
    def add_store(mid, name, cat, desc, addr, emoji, dtime, dfee, rating):
        cur = conn.execute(
            "INSERT INTO stores (manager_id,name,category,description,address,emoji,delivery_time,delivery_fee,rating) VALUES (?,?,?,?,?,?,?,?,?)",
            (mid,name,cat,desc,addr,emoji,dtime,dfee,rating))
        return cur.lastrowid

    s1 = add_store(m1,'Pizza Palace','food','Authentic Italian, handmade daily','45 Brick Lane','🍕','20-30',1.99,4.8)
    s2 = add_store(m2,'QuickPharm','medicine','24/7 pharmacy, OTC & prescription','12 High St','💊','15-25',0.99,4.9)
    s3 = add_store(m3,'FreshMart','grocery','Fresh groceries in 30 mins','8 Market Rd','🛒','25-40',1.49,4.7)
    s4 = add_store(m4,'Burger Barn','food','Gourmet smash burgers','22 King St','🍔','15-25',1.49,4.6)
    s5 = add_store(m5,'Tokyo Kitchen','food','Fresh sushi & Japanese cuisine','5 Japan St','🍱','25-40',2.49,4.9)
    s6 = add_store(m6,'TechDrop','general','Phone accessories & gadgets','1 Silicon St','📱','30-50',2.99,4.5)

    # Products
    prods = [
        (s1,'Margherita Pizza','Classic tomato & mozzarella',9.99,'🍕'),
        (s1,'Pepperoni Pizza','Loaded with pepperoni',12.99,'🍕'),
        (s1,'BBQ Chicken Pizza','Smoky BBQ + grilled chicken',13.99,'🍕'),
        (s1,'Garlic Bread','Baked with butter & herbs',3.49,'🥖'),
        (s1,'Tiramisu','Authentic Italian dessert',4.99,'🍮'),
        (s2,'Paracetamol 500mg (24)','Pain & fever relief',3.49,'💊'),
        (s2,'Ibuprofen 200mg (16)','Anti-inflammatory',4.29,'💊'),
        (s2,'Vitamin C 1000mg (30)','Immune support',6.99,'🍊'),
        (s2,'Antihistamine (10)','Allergy relief',5.49,'💊'),
        (s2,'Antiseptic Cream 30g','Wound care',4.99,'🩹'),
        (s2,'Cough Syrup 200ml','Dry & chesty cough',6.49,'🧴'),
        (s3,'Whole Milk 2L','Fresh semi-skimmed',1.89,'🥛'),
        (s3,'Sourdough Bread','Artisan baked daily',2.49,'🍞'),
        (s3,'Free Range Eggs x12','Large free range',3.29,'🥚'),
        (s3,'Basmati Rice 1kg','Long grain basmati',2.99,'🍚'),
        (s3,'Chicken Breast 500g','Skinless boneless',4.99,'🍗'),
        (s3,'Mixed Salad Bag','Ready to eat',1.99,'🥬'),
        (s4,'Classic Smash Burger','Double smash with cheese',8.99,'🍔'),
        (s4,'Crispy Chicken Burger','Southern fried chicken',7.99,'🍔'),
        (s4,'Loaded Fries','Cheese sauce & jalapeños',3.99,'🍟'),
        (s4,'Onion Rings','Beer-battered rings',2.49,'🧅'),
        (s4,'Thick Milkshake','Vanilla, choc or strawberry',4.49,'🥤'),
        (s5,'Salmon Sushi Box (8pc)','Premium salmon nigiri',12.99,'🍣'),
        (s5,'Mixed Rolls (12pc)','Tuna, salmon & avocado',14.99,'🍱'),
        (s5,'Chicken Ramen','Rich tonkotsu broth',10.99,'🍜'),
        (s5,'Gyoza (6pc)','Pan-fried pork dumplings',6.49,'🥟'),
        (s5,'Edamame','Salted steamed soy beans',3.49,'🫘'),
        (s6,'USB-C Cable 2m','Braided fast charge',9.99,'🔌'),
        (s6,'Phone Stand','Adjustable desk holder',7.49,'📱'),
        (s6,'AA Batteries x8','Long-lasting alkaline',5.49,'🔋'),
        (s6,'Screen Protector','Tempered glass, universal',4.99,'📲'),
        (s6,'Wireless Earbuds','Bluetooth 5.0, 24hr battery',24.99,'🎧'),
    ]
    conn.executemany("INSERT INTO products (store_id,name,description,price,emoji) VALUES (?,?,?,?,?)", prods)
    conn.commit()

# ── AUTH HELPERS ──────────────────────────────────────────────────────────────

def login_required(roles=None):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'uid' not in session:
                return redirect('/login')
            if roles and session.get('role') not in roles:
                flash('Access denied.', 'error')
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
        email    = request.form.get('email','').lower().strip()
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
            cur = conn.execute(
                "INSERT INTO users (name,email,phone,password_hash,role) VALUES (?,?,?,?,?)",
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

# ── CUSTOMER ROUTES ───────────────────────────────────────────────────────────

@app.route('/home')
@login_required(roles=['customer'])
def customer_home():
    return render_template('customer/home.html', user=me())

# ── MANAGER ROUTES ────────────────────────────────────────────────────────────

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

# ── DRIVER ROUTES ─────────────────────────────────────────────────────────────

@app.route('/driver')
@login_required(roles=['driver'])
def driver_dashboard():
    conn = get_db()
    drv = conn.execute("SELECT * FROM drivers WHERE user_id=?", (session['uid'],)).fetchone()
    conn.close()
    return render_template('driver/dashboard.html', user=me(), driver=dict(drv) if drv else {})

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
    return jsonify(stores)

@app.route('/api/stores/<int:sid>/products')
def api_store_products(sid):
    conn = get_db()
    rows = conn.execute("SELECT * FROM products WHERE store_id=? AND available=1 ORDER BY id", (sid,)).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d['is_available'] = bool(d.get('available', 1))
        result.append(d)
    return jsonify(result)

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
               'delivery_time','min_order','delivery_fee','is_open']
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
    cur = conn.execute(
        "INSERT INTO products (store_id,name,description,price,emoji,image_url,available) VALUES (?,?,?,?,?,?,?)",
        (store['id'], data.get('name',''), data.get('description',''),
         float(data.get('price',0)), data.get('emoji','📦'), data.get('image_url',''), avail))
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
        data    = request.json or {}
        # Support is_available alias
        if 'is_available' in data:
            data['available'] = 1 if data.pop('is_available') else 0
        allowed = ['name','description','price','emoji','image_url','available']
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
            "FROM orders o "
            "LEFT JOIN drivers drv ON o.driver_id=drv.id "
            "LEFT JOIN users u ON drv.user_id=u.id "
            "WHERE o.customer_id=? ORDER BY o.created_at DESC", (uid,)).fetchall()

    elif role == 'manager':
        store = conn.execute("SELECT id FROM stores WHERE manager_id=?", (uid,)).fetchone()
        if not store:
            conn.close()
            return jsonify([])
        status = request.args.get('status','')
        if status and status != 'all':
            rows = conn.execute(
                "SELECT o.*, u.name as driver_name FROM orders o "
                "LEFT JOIN drivers drv ON o.driver_id=drv.id "
                "LEFT JOIN users u ON drv.user_id=u.id "
                "WHERE o.store_id=? AND o.status=? ORDER BY o.created_at DESC",
                (store['id'], status)).fetchall()
        else:
            rows = conn.execute(
                "SELECT o.*, u.name as driver_name FROM orders o "
                "LEFT JOIN drivers drv ON o.driver_id=drv.id "
                "LEFT JOIN users u ON drv.user_id=u.id "
                "WHERE o.store_id=? ORDER BY o.created_at DESC",
                (store['id'],)).fetchall()

    elif role == 'driver':
        drv = conn.execute("SELECT id FROM drivers WHERE user_id=?", (uid,)).fetchone()
        if not drv:
            conn.close()
            return jsonify([])
        rows = conn.execute(
            "SELECT * FROM orders WHERE driver_id=? AND status NOT IN ('delivered','cancelled') "
            "ORDER BY created_at DESC", (drv['id'],)).fetchall()
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
    sname = store['name']        if store else 'Unknown'
    user  = conn.execute("SELECT * FROM users WHERE id=?", (session['uid'],)).fetchone()
    sub   = round(sum(i['price'] * i['qty'] for i in items), 2)
    total = round(sub + dfee, 2)
    addr    = data.get('address') or user['address'] or ''
    pay_mtd = data.get('payment_method', 'cash')
    cur   = conn.execute(
        "INSERT INTO orders (customer_id,customer_name,customer_phone,customer_address,"
        "store_id,store_name,items_json,subtotal,delivery_fee,total,notes,payment_method) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (session['uid'], user['name'], user['phone'], addr,
         sid, sname, json.dumps(items), sub, dfee, total, data.get('notes',''), pay_mtd))
    oid = cur.lastrowid
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'order_id': oid, 'total': total}), 201

@app.route('/api/orders/<int:oid>')
def api_order(oid):
    conn = get_db()
    row  = conn.execute(
        "SELECT o.*, u.name as driver_name, u.phone as driver_phone "
        "FROM orders o "
        "LEFT JOIN drivers drv ON o.driver_id=drv.id "
        "LEFT JOIN users u ON drv.user_id=u.id "
        "WHERE o.id=?", (oid,)).fetchone()
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
        conn.execute("UPDATE drivers SET status='busy',current_order_id=? WHERE id=?",
                     (oid, driver_id))
    else:
        conn.execute("UPDATE orders SET status=?,updated_at=? WHERE id=?",
                     (new_status, now, oid))
    if new_status in ('delivered','cancelled'):
        conn.execute("UPDATE drivers SET status='available',current_order_id=NULL WHERE current_order_id=?", (oid,))
        if new_status == 'delivered':
            row = conn.execute("SELECT driver_id FROM orders WHERE id=?", (oid,)).fetchone()
            if row and row['driver_id']:
                conn.execute("UPDATE drivers SET earnings=earnings+3.0,total_deliveries=total_deliveries+1 WHERE id=?",
                             (row['driver_id'],))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── API: DRIVERS ──────────────────────────────────────────────────────────────

@app.route('/api/drivers')
@login_required(roles=['manager'])
def api_drivers():
    status = request.args.get('status','')
    conn   = get_db()
    if status:
        rows = conn.execute(
            "SELECT d.*,u.name,u.phone FROM drivers d JOIN users u ON d.user_id=u.id WHERE d.status=?",
            (status,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT d.*,u.name,u.phone FROM drivers d JOIN users u ON d.user_id=u.id").fetchall()
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
    drv  = conn.execute(
        "SELECT d.*, u.name, u.phone FROM drivers d JOIN users u ON d.user_id=u.id WHERE d.user_id=?",
        (session['uid'],)).fetchone()
    if not drv:
        conn.close()
        return jsonify({'error': 'No driver profile'}), 404
    if request.method == 'PATCH':
        data = request.json or {}
        # Accept both {is_online: bool} and {status: string}
        if 'is_online' in data:
            new_status = 'available' if data['is_online'] else 'offline'
            conn.execute("UPDATE drivers SET status=? WHERE id=?", (new_status, drv['id']))
            conn.commit()
        elif 'status' in data:
            conn.execute("UPDATE drivers SET status=? WHERE id=?", (data['status'], drv['id']))
            conn.commit()
        drv = conn.execute("SELECT d.*, u.name, u.phone FROM drivers d JOIN users u ON d.user_id=u.id WHERE d.user_id=?", (session['uid'],)).fetchone()
    conn.close()
    result = dict(drv)
    result['is_online'] = result.get('status') not in ('offline', None)
    return jsonify(result)

@app.route('/api/drivers/me/orders')
@login_required(roles=['driver'])
def api_driver_orders():
    conn = get_db()
    drv  = conn.execute("SELECT id FROM drivers WHERE user_id=?", (session['uid'],)).fetchone()
    if not drv:
        conn.close()
        return jsonify([])
    # Return all orders for this driver (active + recent delivered)
    rows = conn.execute(
        "SELECT o.*, s.name as store_name FROM orders o "
        "LEFT JOIN stores s ON o.store_id=s.id "
        "WHERE o.driver_id=? ORDER BY o.created_at DESC LIMIT 100",
        (drv['id'],)).fetchall()
    conn.close()
    results = [dict(r) for r in rows]
    # Add address alias for driver dashboard
    for r in results:
        r['address'] = r.get('customer_address', '')
        r['customer_phone'] = r.get('customer_phone', '')
        return jsonify(results)

# ── API: DRIVER PENDING ORDERS ────────────────────────────────────────────────

@app.route('/api/drivers/pending-orders')
@login_required(roles=['driver'])
def api_driver_pending_orders():
    """Returns orders in accepted/preparing state with no driver assigned yet."""
    conn = get_db()
    rows = conn.execute(
        "SELECT o.*, s.name as store_name FROM orders o "
        "LEFT JOIN stores s ON o.store_id=s.id "
        "WHERE o.status IN ('accepted','preparing') AND o.driver_id IS NULL "
        "ORDER BY o.created_at ASC"
    ).fetchall()
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
        return jsonify({'error': 'Already claimed by another driver'}), 409
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute("UPDATE orders SET driver_id=?,status='picked_up',updated_at=? WHERE id=?",
                 (drv['id'], now, oid))
    conn.execute("UPDATE drivers SET status='busy',current_order_id=? WHERE id=?",
                 (oid, drv['id']))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'order_id': oid})

@app.route('/api/orders/<int:oid>/decline', methods=['POST'])
@login_required(roles=['driver'])
def api_driver_decline_order(oid):
    return jsonify({'ok': True, 'order_id': oid})

# ── API: REVIEWS ──────────────────────────────────────────────────────────────

@app.route('/api/orders/<int:oid>/review', methods=['POST'])
@login_required(roles=['customer'])
def api_review_order(oid):
    data = request.json or {}
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id=? AND customer_id=?",
                         (oid, session['uid'])).fetchone()
    if not order:
        conn.close()
        return jsonify({'error': 'Order not found'}), 404
    user = conn.execute("SELECT name FROM users WHERE id=?", (session['uid'],)).fetchone()
    cname = user['name'] if user else 'Customer'
    conn.execute("""
        INSERT OR REPLACE INTO reviews
          (order_id,customer_id,customer_name,store_id,driver_id,store_rating,driver_rating,comment)
        VALUES (?,?,?,?,?,?,?,?)
    """, (oid, session['uid'], cname, order['store_id'], order['driver_id'],
            int(data.get('store_rating', 5)), int(data.get('driver_rating', 5)),
            data.get('comment', '')))
    # Update store rating
    conn.execute("UPDATE stores SET rating=(SELECT AVG(CAST(store_rating AS REAL)) FROM reviews WHERE store_id=?) WHERE id=?",
                 (order['store_id'], order['store_id']))
    # Update driver rating
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
    rows = conn.execute("""
        SELECT r.*, o.total, o.items_json, o.created_at as order_date
        FROM reviews r
        JOIN orders o ON r.order_id=o.id
        WHERE r.store_id=?
        ORDER BY r.created_at DESC
    """, (store['id'],)).fetchall()
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
    rows = conn.execute("""
        SELECT r.*, o.store_name, o.created_at as order_date
        FROM reviews r
        JOIN orders o ON r.order_id=o.id
        WHERE r.driver_id=?
        ORDER BY r.created_at DESC
    """, (drv['id'],)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

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
    revenue = conn.execute(
        "SELECT COALESCE(SUM(total),0) FROM orders WHERE store_id=? AND status='delivered' AND created_at LIKE ?",
        (sid, today+'%')).fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE store_id=? AND status NOT IN ('delivered','cancelled')",
        (sid,)).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM orders WHERE store_id=?", (sid,)).fetchone()[0]
    delivered = conn.execute("SELECT COUNT(*) FROM orders WHERE store_id=? AND status='delivered'", (sid,)).fetchone()[0]
    conn.close()
    return jsonify({'revenue':revenue,'pending':pending,'total':total,'delivered':delivered})

init_db()

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
