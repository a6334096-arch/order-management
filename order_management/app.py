from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
import sqlite3, os
from functools import wraps
from datetime import date
from werkzeug.security import generate_password_hash, check_password_hash
import qrcode

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, 'orders.db')
QR_DIR = os.path.join(BASE, 'static', 'qrcodes')
os.makedirs(QR_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-me')
STATUSES = ['處理中', '已出貨', '已完成', '已取消']


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db():
    conn = db()
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS admin (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS customer (
      客戶編號 INTEGER PRIMARY KEY,
      名稱 TEXT NOT NULL,
      電話 TEXT,
      地址 TEXT
    );
    CREATE TABLE IF NOT EXISTS product (
      商品編號 INTEGER PRIMARY KEY,
      名稱 TEXT NOT NULL,
      單價 REAL NOT NULL CHECK(單價 >= 0),
      庫存 INTEGER NOT NULL CHECK(庫存 >= 0),
      分類 TEXT
    );
    CREATE TABLE IF NOT EXISTS orders (
      訂單編號 INTEGER PRIMARY KEY,
      客戶編號 INTEGER NOT NULL,
      訂單日期 TEXT NOT NULL,
      狀態 TEXT NOT NULL CHECK(狀態 IN ('處理中','已出貨','已完成','已取消')),
      業務人員 TEXT,
      FOREIGN KEY(客戶編號) REFERENCES customer(客戶編號)
    );
    CREATE TABLE IF NOT EXISTS order_item (
      訂單編號 INTEGER NOT NULL,
      商品編號 INTEGER NOT NULL,
      數量 INTEGER NOT NULL CHECK(數量 > 0),
      單價 REAL NOT NULL CHECK(單價 >= 0),
      PRIMARY KEY(訂單編號, 商品編號),
      FOREIGN KEY(訂單編號) REFERENCES orders(訂單編號) ON DELETE CASCADE,
      FOREIGN KEY(商品編號) REFERENCES product(商品編號)
    );
    ''')
    if conn.execute('SELECT COUNT(*) FROM admin').fetchone()[0] == 0:
        conn.execute('INSERT INTO admin(username,password_hash) VALUES (?,?)', ('admin', generate_password_hash('admin123')))
    if conn.execute('SELECT COUNT(*) FROM customer').fetchone()[0] == 0:
        conn.executemany('INSERT INTO customer VALUES (?,?,?,?)', [
          (1,'王小明','0912-345-678','台北市中正區忠孝東路一段10號'),
          (2,'陳美玲','0922-111-222','新北市板橋區文化路二段88號'),
          (3,'林志宏','0933-456-789','桃園市桃園區中正路120號'),
          (4,'張雅婷','0955-888-666','台中市西屯區台灣大道三段99號'),
          (5,'黃俊傑','0988-123-456','高雄市左營區博愛二路168號')])
    if conn.execute('SELECT COUNT(*) FROM product').fetchone()[0] == 0:
        conn.executemany('INSERT INTO product VALUES (?,?,?,?,?)', [
          (101,'無線滑鼠',590,50,'電腦周邊'),(102,'機械式鍵盤',1890,30,'電腦周邊'),
          (103,'USB-C 集線器',1290,25,'電腦周邊'),(104,'27吋顯示器',6990,12,'顯示設備'),
          (105,'筆記型電腦支架',890,40,'辦公用品')])
    if conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 0:
        conn.executemany('INSERT INTO orders VALUES (?,?,?,?,?)', [
          (1001,1,'2026-10-01','已完成','李佳豪'),(1002,2,'2026-10-02','處理中','李佳豪'),
          (1003,3,'2026-10-03','已出貨','吳佩珊'),(1004,4,'2026-10-04','處理中','吳佩珊'),
          (1005,5,'2026-10-05','已完成','陳冠宇')])
    if conn.execute('SELECT COUNT(*) FROM order_item').fetchone()[0] == 0:
        # 5 筆明細；1001 為多項商品案例。單價是下單當時價格快照。
        conn.executemany('INSERT INTO order_item VALUES (?,?,?,?)', [
          (1001,101,2,590),(1001,102,1,1890),(1002,103,1,1290),(1003,104,2,6990),(1005,105,3,890)])
    conn.commit(); conn.close()


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('admin'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        conn=db(); u=conn.execute('SELECT * FROM admin WHERE username=?',(request.form['username'],)).fetchone(); conn.close()
        if u and check_password_hash(u['password_hash'], request.form['password']):
            session['admin']=u['username']; return redirect(url_for('index'))
        flash('帳號或密碼錯誤','danger')
    return render_template('login.html')

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    conn = db()

    # 1. 累計營收（排除已取消）
    total_revenue = conn.execute('''
        SELECT COALESCE(SUM(oi.數量 * oi.單價), 0)
        FROM orders o
        JOIN order_item oi USING(訂單編號)
        WHERE o.狀態 != '已取消'
    ''').fetchone()[0]

    # 2. 有效訂單數
    valid_orders = conn.execute('''
        SELECT COUNT(*)
        FROM orders
        WHERE 狀態 != '已取消'
    ''').fetchone()[0]

    # 3. 平均客單價
    avg_order_value = total_revenue / valid_orders if valid_orders else 0

    # 4. 客戶數
    customer_count = conn.execute(
        'SELECT COUNT(*) FROM customer'
    ).fetchone()[0]

    # 5. 每月營收
    monthly = conn.execute('''
        SELECT substr(o.日期, 1, 7) AS 月份,
               COALESCE(SUM(oi.數量 * oi.單價), 0) AS 營收
        FROM orders o
        JOIN order_item oi USING(訂單編號)
        WHERE o.狀態 != '已取消'
        GROUP BY substr(o.日期, 1, 7)
        ORDER BY 月份
    ''').fetchall()

    monthly_labels = [row['月份'] for row in monthly]
    monthly_revenue = [row['營收'] for row in monthly]

    # 6. 訂單狀態分布
    status_data = conn.execute('''
        SELECT 狀態, COUNT(*) AS 數量
        FROM orders
        GROUP BY 狀態
        ORDER BY 數量 DESC
    ''').fetchall()

    status_labels = [row['狀態'] for row in status_data]
    status_counts = [row['數量'] for row in status_data]

    # 7. 熱銷商品 Top 5
    top_products = conn.execute('''
        SELECT p.名稱 AS 商品名稱,
               SUM(oi.數量) AS 售出數量,
               SUM(oi.數量 * oi.單價) AS 營收
        FROM order_item oi
        JOIN orders o USING(訂單編號)
        JOIN product p USING(商品編號)
        WHERE o.狀態 != '已取消'
        GROUP BY p.商品編號, p.名稱
        ORDER BY 售出數量 DESC
        LIMIT 5
    ''').fetchall()

    # 8. 客戶消費排行 Top 5
    top_customers = conn.execute('''
        SELECT c.名稱 AS 客戶名稱,
               COUNT(DISTINCT o.訂單編號) AS 訂單數,
               SUM(oi.數量 * oi.單價) AS 消費金額
        FROM customer c
        JOIN orders o USING(客戶編號)
        JOIN order_item oi USING(訂單編號)
        WHERE o.狀態 != '已取消'
        GROUP BY c.客戶編號, c.名稱
        ORDER BY 消費金額 DESC
        LIMIT 5
    ''').fetchall()

    conn.close()

    return render_template(
        'index.html',
        total_revenue=total_revenue,
        valid_orders=valid_orders,
        avg_order_value=avg_order_value,
        customer_count=customer_count,
        monthly_labels=monthly_labels,
        monthly_revenue=monthly_revenue,
        status_labels=status_labels,
        status_counts=status_counts,
        top_products=top_products,
        top_customers=top_customers
    )



@app.route('/customers')
@login_required
def customers():
    conn=db(); rows=conn.execute('SELECT * FROM customer ORDER BY 客戶編號').fetchall(); conn.close(); return render_template('customers.html',rows=rows)

@app.route('/customer/new', methods=['GET','POST'])
@app.route('/customer/<int:id>/edit', methods=['GET','POST'])
@login_required
def customer_form(id=None):
    conn=db(); row=conn.execute('SELECT * FROM customer WHERE 客戶編號=?',(id,)).fetchone() if id else None
    if request.method=='POST':
        try:
            if id: conn.execute('UPDATE customer SET 名稱=?,電話=?,地址=? WHERE 客戶編號=?',(request.form['name'],request.form['phone'],request.form['address'],id))
            else: conn.execute('INSERT INTO customer VALUES (?,?,?,?)',(int(request.form['id']),request.form['name'],request.form['phone'],request.form['address']))
            conn.commit(); flash('客戶資料已儲存','success'); conn.close(); return redirect(url_for('customers'))
        except sqlite3.IntegrityError as e: flash(f'儲存失敗：{e}','danger')
    conn.close(); return render_template('customer_form.html',row=row)

@app.post('/customer/<int:id>/delete')
@login_required
def customer_delete(id):
    conn=db()
    try: conn.execute('DELETE FROM customer WHERE 客戶編號=?',(id,)); conn.commit(); flash('客戶已刪除','success')
    except sqlite3.IntegrityError: flash('此客戶已有訂單，無法刪除','danger')
    conn.close(); return redirect(url_for('customers'))

@app.route('/products')
@login_required
def products():
    conn=db(); rows=conn.execute('SELECT * FROM product ORDER BY 商品編號').fetchall(); conn.close(); return render_template('products.html',rows=rows)

@app.route('/product/new', methods=['GET','POST'])
@app.route('/product/<int:id>/edit', methods=['GET','POST'])
@login_required
def product_form(id=None):
    conn=db(); row=conn.execute('SELECT * FROM product WHERE 商品編號=?',(id,)).fetchone() if id else None
    if request.method=='POST':
        vals=(request.form['name'],float(request.form['price']),int(request.form['stock']),request.form['category'])
        try:
            if id: conn.execute('UPDATE product SET 名稱=?,單價=?,庫存=?,分類=? WHERE 商品編號=?',(*vals,id))
            else: conn.execute('INSERT INTO product VALUES (?,?,?,?,?)',(int(request.form['id']),*vals))
            conn.commit(); flash('商品資料已儲存','success'); conn.close(); return redirect(url_for('products'))
        except (sqlite3.IntegrityError,ValueError) as e: flash(f'儲存失敗：{e}','danger')
    conn.close(); return render_template('product_form.html',row=row)

@app.post('/product/<int:id>/delete')
@login_required
def product_delete(id):
    conn=db()
    try: conn.execute('DELETE FROM product WHERE 商品編號=?',(id,)); conn.commit(); flash('商品已刪除','success')
    except sqlite3.IntegrityError: flash('商品已有歷史訂單，無法刪除','danger')
    conn.close(); return redirect(url_for('products'))

@app.route('/orders')
@login_required
def orders_list():
    conn=db(); rows=conn.execute('''SELECT o.*,c.名稱 客戶名稱,COALESCE(SUM(i.數量*i.單價),0) 總額 FROM orders o JOIN customer c USING(客戶編號) LEFT JOIN order_item i USING(訂單編號) GROUP BY o.訂單編號 ORDER BY o.訂單日期 DESC,o.訂單編號 DESC''').fetchall(); conn.close()
    return render_template('orders.html',rows=rows,statuses=STATUSES)

@app.route('/order/new', methods=['GET','POST'])
@login_required
def order_new():
    conn=db(); customers=conn.execute('SELECT * FROM customer ORDER BY 名稱').fetchall(); products=conn.execute('SELECT * FROM product ORDER BY 商品編號').fetchall()
    if request.method=='POST':
        selected=request.form.getlist('products')
        try:
            if not selected: raise ValueError('請至少選擇一項商品')
            oid=int(request.form['order_id']); cid=int(request.form['customer_id'])
            conn.execute('INSERT INTO orders VALUES (?,?,?,?,?)',(oid,cid,request.form['date'],request.form['status'],request.form['sales']))
            for pid_s in selected:
                pid=int(pid_s); qty=int(request.form.get(f'qty_{pid}',0))
                if qty<=0: raise ValueError('已勾選商品的數量必須大於 0')
                p=conn.execute('SELECT 單價,庫存 FROM product WHERE 商品編號=?',(pid,)).fetchone()
                if not p or qty>p['庫存']: raise ValueError(f'商品 {pid} 庫存不足')
                conn.execute('INSERT INTO order_item VALUES (?,?,?,?)',(oid,pid,qty,p['單價']))
                conn.execute('UPDATE product SET 庫存=庫存-? WHERE 商品編號=?',(qty,pid))
            conn.commit(); flash('訂單新增完成','success'); conn.close(); return redirect(url_for('order_detail',id=oid))
        except (ValueError,sqlite3.IntegrityError) as e:
            conn.rollback(); flash(f'新增失敗：{e}','danger')
    conn.close(); return render_template('order_new.html',customers=customers,products=products,statuses=STATUSES,today=date.today().isoformat())

@app.post('/order/<int:id>/status')
@login_required
def order_status(id):
    status=request.form['status']
    if status not in STATUSES: abort(400)
    conn=db(); conn.execute('UPDATE orders SET 狀態=? WHERE 訂單編號=?',(status,id)); conn.commit(); conn.close(); flash('訂單狀態已更新','success'); return redirect(request.referrer or url_for('orders_list'))

@app.post('/order/<int:id>/delete')
@login_required
def order_delete(id):
    conn=db(); conn.execute('DELETE FROM orders WHERE 訂單編號=?',(id,)); conn.commit(); conn.close(); flash('訂單已刪除','success'); return redirect(url_for('orders_list'))

@app.route('/order/<int:id>')
@login_required
def order_detail(id):
    conn=db(); order=conn.execute('''SELECT o.*,c.名稱 客戶名稱,c.電話,c.地址 FROM orders o JOIN customer c USING(客戶編號) WHERE 訂單編號=?''',(id,)).fetchone()
    if not order: conn.close(); abort(404)
    items=conn.execute('''SELECT i.*,p.名稱 商品名稱 FROM order_item i JOIN product p USING(商品編號) WHERE 訂單編號=? ORDER BY 商品編號''',(id,)).fetchall(); conn.close()
    total=sum(x['數量']*x['單價'] for x in items)
    qr_name=f'order_{id}.png'; qr_path=os.path.join(QR_DIR,qr_name)
    qr_url=request.url
    qrcode.make(qr_url).save(qr_path)
    return render_template('order_detail.html',order=order,items=items,total=total,qr_name=qr_name,statuses=STATUSES)

init_db()

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)
