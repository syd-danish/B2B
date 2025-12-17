from flask import (Flask, render_template, request,
                   flash, session, redirect,
                   url_for, jsonify)
import sqlite3, random, smtplib, os, json, re
from email.mime.text import MIMEText
from dotenv import load_dotenv
from urllib.parse import quote
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from werkzeug.utils import secure_filename
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_APP_SECRET_KEY", "dev-secret-key")
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
os.makedirs(INSTANCE_DIR, exist_ok=True)
DATABASE = os.path.join(INSTANCE_DIR, "database.db")
app.config['DATABASE'] = DATABASE
print("Database set to:", app.config.get("DATABASE"))
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
AUTHORIZED_CLIENTS=[]

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5MB max file size
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads", "products")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def init_db():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("""CREATE TABLE IF NOT EXISTS otps (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            identifier TEXT,
                            otp TEXT,
                            expires_at DATETIME
                        )""")

    # Create tables if they don't exist
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE,
        client_name TEXT,
        phone TEXT,
        address TEXT,
        company TEXT,
        user_type TEXT DEFAULT 'client'
    )
    """)

    # Check if new columns exist and add them if they don't
    cursor.execute("PRAGMA table_info(users)")
    columns = [column[1] for column in cursor.fetchall()]
    for column, column_type in [
        ('client_name', 'TEXT'),
        ('phone', 'TEXT'),
        ('address', 'TEXT'),
        ('company', 'TEXT'),
        ('user_type', "TEXT DEFAULT 'client'")
    ]:
        if column not in columns:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {column} {column_type}")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            category TEXT NOT NULL,
            product_options TEXT,
            product_rate TEXT,
            stock_status TEXT NOT NULL,
            image_filename TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)
    cursor.execute("""
           CREATE TABLE IF NOT EXISTS admins (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               email TEXT UNIQUE NOT NULL,
               created_at DATETIME DEFAULT CURRENT_TIMESTAMP
           )
       """)
    # Insert the main admin if not exists
    main_admin_email = os.getenv("ADMIN_EMAIL")
    if main_admin_email:
        cursor.execute("INSERT OR IGNORE INTO admins (email) VALUES (?)", (main_admin_email.lower(),))
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT,
            expected_date TEXT,
            quantity TEXT,
            comments TEXT,
            user_email TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER,
        user_email TEXT,
        subject TEXT,
        body TEXT,
        attachment_name TEXT,
        order_name TEXT,
        order_quantity TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (order_id) REFERENCES orders(id)
    )
    """)
    conn.commit()
    conn.close()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def add_client(email, client_name, phone, address, company):
    """Add a new client to the database"""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    try:
        cursor.execute("""INSERT INTO users (email, client_name, phone, address, company, user_type) 
                         VALUES (?, ?, ?, ?, ?, ?)""",
                       (email.lower(), client_name, phone, address, company, "client"))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False # Email already exists
    except Exception as e:
        print(f"Database error: {e}")
        return False
    finally:
        conn.close()

def validate_email(email):
    """Validate email format"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def validate_phone(phone):
    """Validate phone number format (basic validation)"""
    # Remove spaces, dashes, and parentheses
    cleaned_phone = re.sub(r'[\s\-\(\)]', '', phone)
    # Check if it contains only digits and has reasonable length
    return len(cleaned_phone) >= 8 and len(cleaned_phone) <= 15 and cleaned_phone.isdigit()



def get_all_clients():
    """Get all clients from the database"""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, email, client_name, phone, address, company FROM users WHERE user_type = 'client'")
    clients = cursor.fetchall()
    conn.close()
    return clients


def delete_client(client_id):
    """Delete a client from the database"""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE id = ? AND user_type = 'client'", (client_id,))
    conn.commit()
    conn.close()


def get_client_by_id(client_id):
    """Get a single client by ID"""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, email, client_name, phone, address, company FROM users WHERE id = ? AND user_type = 'client'", (client_id,))
    client = cursor.fetchone()
    conn.close()
    return client


def update_client(client_id, email, client_name, phone, address, company):
    """Update a client's information"""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    try:
        cursor.execute("""UPDATE users SET email = ?, client_name = ?, phone = ?, address = ?, company = ? 
                         WHERE id = ? AND user_type = 'client'""",
                       (email.lower(), client_name, phone, address, company, client_id))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False  # Email already exists for another client
    finally:
        conn.close()
@app.route("/admin/edit-client/<int:client_id>")
def edit_client_route(client_id):
    """Edit client route - redirects to manage_clients with edit parameter"""
    if not session.get("authenticated"):
        session.clear()
        flash("Please login to access the admin panel")
        return redirect(url_for("login"))
    user_email = session.get("user_email")
    if not user_email or not is_admin_in_db(user_email):
        session.clear()
        flash("Admin access required")
        return redirect(url_for("login"))
    return redirect(url_for("manage_clients", edit=client_id))


@app.route("/admin/manage-clients", methods=["GET", "POST"])
def manage_clients():
    """Manage clients section - requires admin authentication"""
    if not session.get("authenticated"):
        session.clear()
        flash("Please login to access the admin panel")
        return redirect(url_for("login"))

    user_email = session.get("user_email")
    if not user_email or not is_admin_in_db(user_email):
        session.clear()
        flash("Admin access required")
        return redirect(url_for("login"))

    # Handle edit client request
    edit_client = None
    if request.args.get("edit"):
        client_id = request.args.get("edit")
        edit_client = get_client_by_id(client_id)
        if not edit_client:
            flash("Client not found")
            return redirect(url_for("manage_clients"))

    if request.method == "POST":
        # Check if this is an edit or add operation
        client_id = request.form.get("client_id")

        email = request.form.get("email", "").strip().lower()
        client_name = request.form.get("client_name", "").strip()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        company = request.form.get("company", "").strip()

        # Validate required fields
        if not all([email, client_name, phone, company]):
            flash("Please fill in all required fields")
        elif "@" not in email:
            flash("Please enter a valid email address")
        else:
            if client_id:  # This is an edit operation
                if update_client(client_id, email, client_name, phone, address, company):
                    flash(f"Client {client_name} updated successfully!")
                    return redirect(url_for("manage_clients"))
                else:
                    flash("Email already exists for another client. Please use a different email.")
                    edit_client = get_client_by_id(client_id)
            else:
                if add_client(email, client_name, phone, address, company):
                    flash(f"Client {client_name} added successfully!")
                else:
                    flash("Email already exists. Please use a different email.")

    # Get all clients to display
    clients = get_all_clients()
    return render_template("admin.html", section="manage-clients", clients=clients, edit_client=edit_client)

@app.route("/admin/delete-client/<int:client_id>", methods=["POST"])
def delete_client_route(client_id):
    """Delete a client - requires admin authentication"""
    if not session.get("authenticated"):
        session.clear()
        flash("Please login to access the admin panel")
        return redirect(url_for("login"))

    user_email = session.get("user_email")
    if not user_email or not is_admin_in_db(user_email):
        session.clear()
        flash("Admin access required")
        return redirect(url_for("login"))

    delete_client(client_id)
    flash("Client deleted successfully!")
    return redirect(url_for("manage_clients"))

def is_authorized_client(email):
    """Check if the email belongs to an authorized client"""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT email FROM users WHERE email = ? AND user_type = 'client'", (email,))
    result = cursor.fetchone()
    conn.close()
    return result is not None or email in AUTHORIZED_CLIENTS

def send_email(receiver, content, subject="Elfit Arabia Login OTP"):
    """Send email with customizable content"""
    sender = os.getenv('SENDER_GMAIL_ADDRS')
    password = os.getenv('GMAIL_APP_PASSWORD_1')
    if not sender or not password:
        raise Exception("Missing Gmail credentials in .env")
    msg = MIMEText(content)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = receiver
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise Exception("Failed to authenticate with Gmail. Check your email and App Password.")
    except Exception as e:
        raise Exception(f"Failed to send email: {str(e)}")


def send_otp_email(receiver, otp):
    """Send OTP email"""
    content = f"Your OTP for Elfit Arabia B2B Portal is: {otp}\n\nThis OTP is valid for 10 minutes."
    send_email(receiver, content, "Elfit Arabia Login OTP")


def send_access_denied_email(receiver):
    """Send access denied email"""
    content = """Access Request Denied

Your request to access the Elfit Arabia B2B Portal has been denied. 
This portal is restricted to authorized personnel only.

If you believe this is an error, please contact our support team.

Best regards,
Elfit Arabia Team"""
    send_email(receiver, content, "Elfit Arabia - Access Denied")


@app.route("/admin/manage-products")
def manage_products():
    if not session.get("authenticated"):
        flash("Please login to access the admin panel")
        return redirect(url_for("login"))
    if not session.get("is_admin") or not is_admin_in_db(session.get("user_email")):
        flash("Admin access required")
        return redirect(url_for("dashboard"))

    all_categories = [
        "Winches", "Cable Drum Trailers", "Rollers", "Cable Drum Lifting Jacks", "Cable Locators", "Reeling Machine",
        "Cable Pulling Grips & Swivel Link", "Duct Rods", "Hydraulic Cutting and Crimping Tools"
        ,"Warning Tapes", "Manhole", "Ropes", "Duct",
        "Electrical", "Solar", "Pipes", "Optical Fibre Cables",
        "Optical Fiber Connectors","Optical Fiber Adapters", "Optical Fiber Consumable",
        "Optical Fiber Instruments", "Optical Distribution Frames", "Optical Fiber Patch Cord",
        "Optical Fibre Tools", "Cabinets","Cable Joint Products","Cable & Wires","Connectors",
        "Distribution Boxes", "Ducts Accessories","Manhole Accessories","Marking & Protection","Earthing Hardware",
        "Miscellaneous","Poles & Accessories" , "Tapes", "Terminal Blocks","Test & Measurement",
                                                                           "Telecom Tools" ,"Other Products"
    ]
    # Connect to database
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM products ORDER BY category, product_name")
    products_raw = cursor.fetchall()
    conn.close()
    # Convert products to dictionaries
    products_list = []
    for p in products_raw:
        try:
            options = json.loads(p[3]) if p[3] else {}
        except json.JSONDecodeError:
            options = {}
        products_list.append({
            "id": p[0],
            "name": p[1],
            "category": p[2],
            "options": options,
            "rate": p[4] if p[4] else None,
            "stock": p[5] if p[5] else "out_of_stock",
            "image": p[6] if p[6] else None
        })
    products_by_category = {category: [] for category in all_categories}
    for product in products_list:
        matched = False
        for cat in all_categories:
            if product["category"].lower().strip() == cat.lower().strip():
                products_by_category[cat].append(product)
                matched = True
                break
        if not matched:
            products_by_category["Other Products"].append(product)
    for product in products_list:
        print(f'products_list: {product}')
    return render_template(
        "admin.html",
        section="manage-products",
        all_categories=all_categories,
        products_by_category=products_by_category)


@app.route("/admin/add-product", methods=['GET', 'POST'])
def add_product():
    if not session.get("authenticated"):
        flash("Please login to access the admin panel")
        return redirect(url_for("login"))
    if not session.get("is_admin") or not is_admin_in_db(session.get("user_email")):
        flash("Admin access required")
        return redirect(url_for("dashboard"))
    if request.method == 'POST':
        try:
            product_name = request.form.get('product_name')
            category = request.form.get('category')
            product_rate = request.form.get('product_rate')
            stock_status = request.form.get('stock_status')
            option_names = request.form.getlist('product_options[]')
            option_values = request.form.getlist('option_values[]')
            options = {}
            for i, name in enumerate(option_names):
                if name.strip() and i < len(option_values) and option_values[i].strip():
                    options[name.strip()] = option_values[i].strip()

            options_json = json.dumps(options if options else None)

            image_filename = None
            if 'product_image' in request.files:
                file = request.files['product_image']
                if file and file.filename != '' and allowed_file(file.filename):
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    filename = secure_filename(file.filename)
                    name, ext = os.path.splitext(filename)
                    image_filename = f"{timestamp}_{name}{ext}"
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)
                    file.save(file_path)

            conn = sqlite3.connect(app.config['DATABASE'])
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO products (product_name, category, product_options, product_rate, stock_status, image_filename)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (product_name, category, options_json, product_rate, stock_status, image_filename))
            conn.commit()
            conn.close()

            flash(f"Product '{product_name}' added successfully to {category} category!")
            return redirect(url_for("manage_products"))

        except Exception as e:
            flash(f"Error adding product: {str(e)}")
            return redirect(url_for("manage_products"))

    return render_template("admin.html", section="add-product")


@app.route("/admin/edit-product/<int:product_id>", methods=['GET', 'POST'])
def edit_product(product_id):
    if not session.get("authenticated"):
        flash("Please login to access the admin panel")
        return redirect(url_for("login"))
    if not session.get("is_admin") or not is_admin_in_db(session.get("user_email")):
        flash("Admin access required")
        return redirect(url_for("dashboard"))

    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    if request.method == 'POST':
        try:
            product_name = request.form.get('product_name')
            category = request.form.get('category')
            product_rate = request.form.get('product_rate')
            stock_status = request.form.get('stock_status')

            option_names = request.form.getlist('product_options[]')
            option_values = request.form.getlist('option_values[]')

            options = {}
            for i, name in enumerate(option_names):
                if name.strip() and i < len(option_values) and option_values[i].strip():
                    options[name.strip()] = option_values[i].strip()

            options_json = json.dumps(options) if options else None

            image_filename = None
            if 'product_image' in request.files:
                file = request.files['product_image']
                if file and file.filename != '' and allowed_file(file.filename):
                    cursor.execute("SELECT image_filename FROM products WHERE id = ?", (product_id,))
                    old_image = cursor.fetchone()
                    if old_image and old_image[0]:
                        old_file_path = os.path.join(app.config['UPLOAD_FOLDER'], old_image[0])
                        if os.path.exists(old_file_path):
                            os.remove(old_file_path)

                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    filename = secure_filename(file.filename)
                    name, ext = os.path.splitext(filename)
                    image_filename = f"{timestamp}_{name}{ext}"
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)
                    file.save(file_path)

                    cursor.execute("""
                        UPDATE products 
                        SET product_name = ?, category = ?, product_options = ?, 
                            product_rate = ?, stock_status = ?, image_filename = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (product_name, category, options_json, product_rate, stock_status, image_filename, product_id))
                else:
                    cursor.execute("""
                        UPDATE products 
                        SET product_name = ?, category = ?, product_options = ?, 
                            product_rate = ?, stock_status = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (product_name, category, options_json, product_rate, stock_status, product_id))
            else:
                cursor.execute("""
                    UPDATE products 
                    SET product_name = ?, category = ?, product_options = ?, 
                        product_rate = ?, stock_status = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (product_name, category, options_json, product_rate, stock_status, product_id))

            conn.commit()
            conn.close()

            flash(f"Product '{product_name}' updated successfully!")
            return redirect(url_for("manage_products"))

        except Exception as e:
            flash(f"Error updating product: {str(e)}")
            conn.close()
            return redirect(url_for("manage_products"))

    cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
    product = cursor.fetchone()
    conn.close()

    if not product:
        flash("Product not found")
        return redirect(url_for("manage_products"))
    options = {}
    if product[3]:
        try:
            options = json.loads(product[3])
        except:
            options = {}
    return render_template("admin.html", section="edit-product", edit_product=product, edit_options=options)


@app.route("/admin/delete-product/<int:product_id>", methods=['POST'])
def delete_product(product_id):
    if not session.get("authenticated"):
        flash("Please login to access the admin panel")
        return redirect(url_for("login"))
    if not session.get("is_admin") or not is_admin_in_db(session.get("user_email")):
        flash("Admin access required")
        return redirect(url_for("dashboard"))
    try:
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        cursor.execute("SELECT product_name, image_filename FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()
        if product:
            product_name = product[0]
            image_filename = product[1]
            if image_filename:
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)
                if os.path.exists(file_path):
                    os.remove(file_path)
            cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
            conn.commit()
            flash(f"Product '{product_name}' deleted successfully!")
        else:
            flash("Product not found")
        conn.close()
    except Exception as e:
        flash(f"Error deleting product: {str(e)}")
    return redirect(url_for("manage_products"))

@app.route("/api/products/<category>")
def get_products_by_category(category):
    """Get products by category - API endpoint"""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM products WHERE category = ? ORDER BY product_name", (category,))
    products = cursor.fetchall()
    conn.close()

    # Convert to JSON format
    product_list = []
    for product in products:
        options = {}
        if product[3]:  # product_options
            try:
                options = json.loads(product[3])
            except:
                options = {}
        product_dict = {
            'id': product[0],
            'product_name': product[1],
            'category': product[2],
            'options': options,
            'product_rate': product[4],
            'stock_status': product[5],
            'image_filename': product[6],
            'created_at': product[7],
            'updated_at': product[8]
        }
        product_list.append(product_dict)
    return jsonify(product_list)


@app.route("/", methods=["GET", "POST"])
def login():
    show_otp_section = session.get("otp_sent", False)
    identifier = session.get("identifier", "")
    if request.method == "POST":
        if "identifier" in request.form:  # Handle email/phone submission
            identifier = request.form["identifier"].strip().lower()
            if "@" not in identifier:
                flash("Please enter a valid email address")
                show_otp_section = False
                session["otp_sent"] = False
                return render_template("login.html", show_otp_section=show_otp_section, identifier=identifier)
            session["identifier"] = identifier
            otp = str(random.randint(100000, 999999))
            session["otp"] = otp
            session["otp_sent"] = True
            # Check authorization
            if is_admin_in_db(identifier):
                session["user_type"] = "admin"
                try:
                    send_otp_email(identifier, otp)
                    flash("Admin OTP sent! Please enter it below.")
                    show_otp_section = True
                except Exception as e:
                    flash(f"Error sending OTP: {str(e)}")
                    show_otp_section = False
                    session["otp_sent"] = False
            elif is_authorized_client(identifier):
                session["user_type"] = "client"
                try:
                    send_otp_email(identifier, otp)
                    flash("Client OTP sent! Please enter it below.")
                    show_otp_section = True
                except Exception as e:
                    flash(f"Error sending OTP: {str(e)}")
                    show_otp_section = False
                    session["otp_sent"] = False
            else:
                try:
                    send_access_denied_email(identifier)
                    flash("Access denied. An email notification has been sent.")
                except Exception as e:
                    flash("Access denied.")
                show_otp_section = False
                session["otp_sent"] = False
                session.pop("identifier", None)
        elif "otp" in request.form:  # Handle OTP verification
            entered_otp = request.form["otp"]
            if entered_otp == session.get("otp"):
                user_type = session.get("user_type")
                email = session.get("identifier")
                # Clear temporary session data
                session.pop("otp", None)
                session.pop("otp_sent", None)
                session.pop("user_type", None)
                # Set authenticated session
                session["authenticated"] = True
                session["user_email"] = email
                session["is_admin"] = is_admin_in_db(email)  # Re-validate admin status
                # Redirect based on user type
                if session["is_admin"]:
                    return redirect(url_for("admin"))
                else:
                    return redirect(url_for("dashboard"))
            else:
                flash("Invalid OTP. Please try again or request a new OTP.")
                show_otp_section = True
    return render_template("login.html", show_otp_section=show_otp_section, identifier=identifier)





@app.route("/admin")
def admin():
    if not session.get("authenticated"):
        flash("Please login to access the admin panel")
        return redirect(url_for("login"))
    if not session.get("is_admin") or not is_admin_in_db(session.get("user_email")):
        flash("Admin access required")
        return redirect(url_for("dashboard"))

    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    now_uae = datetime.now(ZoneInfo("Asia/Dubai"))
    one_week_ago = (datetime.now(ZoneInfo("Asia/Dubai")) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    cur.execute("SELECT COUNT(*) FROM orders WHERE datetime(created_at) >= ?", (one_week_ago,))
    orders_this_week = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM orders WHERE status IN ('inquiry received', 'quote sent')")
    unplaced_orders = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'delivered'")
    delivered_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM products WHERE stock_status = 'in_stock'")
    in_stock_products = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM users")
    total_clients = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'dispatched'")
    dispatched_count = cur.fetchone()[0]

    # Pending (not delivered)
    cur.execute("SELECT COUNT(*) FROM orders WHERE status NOT IN ('delivered','inquiry received', 'quote sent')")
    pending_orders = cur.fetchone()[0]

    cur.execute("""
                SELECT product_name, COUNT(*) as inquiries
                FROM orders
                WHERE status IN ('inquiry received', 'received')
                GROUP BY product_name
                ORDER BY inquiries DESC LIMIT 1
                """)
    row = cur.fetchone()
    most_inquired_item = row[0] if row else "No Inquiries Yet"

    cur.execute("""
                SELECT user_email, COUNT(*) as order_count
                FROM orders
                GROUP BY user_email
                ORDER BY order_count DESC LIMIT 1
                """)
    client_row = cur.fetchone()
    most_active_client = client_row[0] if client_row else "No Orders Yet"
    cur.execute("""
                SELECT MIN(created_at) as first_order_date
                FROM orders
                """)
    cur.execute("""
                SELECT COUNT(*)
                FROM orders
                WHERE payment_status = 'unpaid'
                  AND status IN ('order placed', 'dispatched', 'delivered')
                """)
    payment_status_count = cur.fetchone()[0]

    """
    first_order_row = cur.fetchone()
    if first_order_row and first_order_row[0]:
        # Parse the date and format it nicely
        from datetime import datetime
        first_date = datetime.strptime(first_order_row[0], '%Y-%m-%d %H:%M:%S')
        first_order_month = first_date.strftime('%B %Y')  # e.g., "October 2024"
    else:
        first_order_month = None"""


    return render_template(
        "admin.html",
        section="dashboard",unplaced_orders=unplaced_orders,
        orders_this_week=orders_this_week,
        delivered_count=delivered_count,
        dispatched_count=dispatched_count,
        pending_orders=pending_orders,
        in_stock_products=in_stock_products,
        total_clients=total_clients,
        most_inquired_item=most_inquired_item,
    most_active_client = most_active_client, payment_status_count=payment_status_count
    )



def get_all_admins():
    """Get all admins from the database"""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, email, created_at FROM admins ORDER BY email")
    admins = cursor.fetchall()
    conn.close()
    return admins

def add_admin(email):
    """Add a new admin to the database"""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    try:
        # Check if user exists as a client first
        cursor.execute("SELECT id FROM users WHERE email = ? AND user_type = 'client'", (email.lower(),))
        client = cursor.fetchone()

        # Add to admins table
        cursor.execute("INSERT INTO admins (email) VALUES (?)", (email.lower(),))
        # If they were a client, update their user_type to admin
        if client:
            cursor.execute("UPDATE users SET user_type = 'admin' WHERE email = ?", (email.lower(),))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False  # Admin already exists
    finally:
        conn.close()


def remove_admin(admin_id):
    """Remove an admin and convert them to a client"""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    # Get the admin email before deletion
    cursor.execute("SELECT email FROM admins WHERE id = ?", (admin_id,))
    admin = cursor.fetchone()
    if admin:
        admin_email = admin[0]
        # Don't allow removal of main admin
        main_admin_email = os.getenv("ADMIN_EMAIL")
        if admin_email.lower() == main_admin_email.lower():
            conn.close()
            return False, "Cannot remove main admin"

        # Remove from admins table
        cursor.execute("DELETE FROM admins WHERE id = ?", (admin_id,))

        # Check if they exist in users table
        cursor.execute("SELECT id FROM users WHERE email = ?", (admin_email,))
        user = cursor.fetchone()

        if user:
            # Update their user_type to client
            cursor.execute("UPDATE users SET user_type = 'client' WHERE email = ?", (admin_email,))
        else:
            # Add them as a new client with minimal info
            cursor.execute("""INSERT INTO users (email, client_name, phone, address, company, user_type) 
                             VALUES (?, ?, '', '', '', 'client')""",
                           (admin_email, admin_email.split('@')[0]))
        conn.commit()
        conn.close()
        return True, "Admin removed and converted to client"

    conn.close()
    return False, "Admin not found"


def is_admin_in_db(email):
    """Check if the email belongs to an admin"""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT email FROM admins WHERE email = ?", (email.lower(),))
    result = cursor.fetchone()
    conn.close()
    return result is not None

@app.route("/admin_list")
def admin_list():
    """Admin list page - requires admin authentication"""
    if not session.get("authenticated"):
        session.clear()
        flash("Please login to access the admin panel")
        return redirect(url_for("login"))

    user_email = session.get("user_email")
    if not user_email or not is_admin_in_db(user_email):
        session.clear()
        flash("Admin access required")
        return redirect(url_for("login"))

    admins = get_all_admins()
    main_admin_email = os.getenv("ADMIN_EMAIL")

    return render_template("admin_list.html",
                           admins=admins,
                           main_admin_email=main_admin_email)


@app.route("/admin/add-admin", methods=["POST"])
def add_admin_route():
    """Add a new admin - requires admin authentication"""
    if not session.get("authenticated"):
        session.clear()
        flash("Please login to access the admin panel")
        return redirect(url_for("login"))

    user_email = session.get("user_email")
    if not user_email or not is_admin_in_db(user_email):
        session.clear()
        flash("Admin access required")
        return redirect(url_for("login"))
    admin_email = request.form.get("admin_email", "").strip().lower()
    # Validate email
    if not admin_email or "@" not in admin_email:
        flash("Please enter a valid email address")
        return redirect(url_for("admin_list"))
    # Check if already an admin
    if is_admin_in_db(admin_email):
        flash("User is already an admin")
        return redirect(url_for("admin_list"))
    if add_admin(admin_email):
        flash(f"Admin {admin_email} added successfully!")
    else:
        flash("Error adding admin. Please try again.")

    return redirect(url_for("admin_list"))


@app.route("/admin/remove-admin/<int:admin_id>", methods=["POST"])
def remove_admin_route(admin_id):
    """Remove an admin - requires admin authentication"""
    if not session.get("authenticated"):
        session.clear()
        flash("Please login to access the admin panel")
        return redirect(url_for("login"))

    user_email = session.get("user_email")
    if not user_email or not is_admin_in_db(user_email):
        session.clear()
        flash("Admin access required")
        return redirect(url_for("login"))

    success, message = remove_admin(admin_id)
    flash(message)

    return redirect(url_for("admin_list"))


@app.context_processor
def inject_unread_count():
    unread_count = 0
    if session.get("authenticated"):
        user_email = session.get("user_email")
        if user_email:
            unread_count = get_unread_message_count(user_email)
    return dict(unread_count=unread_count)

datetime.now
@app.route("/api/unread-count")
def api_unread_count():
    if not session.get("authenticated"):
        return jsonify({"count": 0})

    user_email = session.get("user_email")
    if not user_email:
        return jsonify({"count": 0})

    count = get_unread_message_count(user_email)
    return jsonify({"count": count})


@app.route("/place_order", methods=["POST"])
def place_order():
    if not session.get("authenticated"):
        flash("Please login to place an order", "warning")
        return redirect(url_for("login"))
    product_id = request.form.get("product_id")
    expected_date = request.form.get("expected_date")
    quantity_value = request.form.get("quantity_value")
    quantity_unit = request.form.get("quantity_unit")
    comments = request.form.get("comments", "")
    user_email = session.get("user_email", "Unknown")
    if not product_id or not expected_date or not quantity_value or not quantity_unit:
        flash("Missing required fields", "danger")
        return redirect(url_for("dashboard"))
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT product_name FROM products WHERE id = ?", (product_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        flash("Invalid product selected", "danger")
        return redirect(url_for("dashboard"))
    product_name = row[0] if row else None
    print(product_name)
    quantity = f"{quantity_value} {quantity_unit}".strip()
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO orders (product_name, expected_date, quantity, comments, user_email,status, last_updated)
        VALUES (?, ?, ?, ?, ?, "inquiry received", datetime('now', '+4 hours'))
    """, (product_name, expected_date, quantity, comments, user_email))
    conn.commit()
    conn.close()
    subject = "New Order Placed"
    body = f"""A new order has been placed:
    Product Name: {product_name}
    Expected Date: {expected_date}
    Quantity: {quantity}
    Comments: {comments}
    Ordered by: {user_email}
    """
    admin_email = os.getenv("ADMIN_EMAIL")
    email_user = os.getenv("SENDER_GMAIL_ADDRS")
    email_pass = os.getenv("GMAIL_APP_PASSWORD_1")
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = email_user
    msg["To"] = admin_email
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(email_user, email_pass)
            server.send_message(msg)
            flash("Order placed successfully!", "success")
    except Exception as e:
        app.logger.error("Error sending order email: %s", e)
        flash("Order saved but email notification failed (check logs).", "warning")
        return redirect(url_for("dashboard"))
    return redirect(url_for("dashboard"))

@app.route("/dashboard")
def dashboard():
    """Client dashboard route - requires client authentication"""
    if not session.get("authenticated"):
        flash("Please login to access the dashboard")
        return redirect(url_for("login"))
    user_email = session.get("user_email")
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM products")
    rows = cursor.fetchall()
    conn.close()
    products_list = []
    for row in rows:
        products_list.append({
            "id": row[0],
            "name": row[1],
            "category": row[2],
            "options": json.loads(row[3]) if row[3] else {},
            "rate": row[4],
            "stock": row[5],
            "image": row[6]
        })
    all_categories = [
        "Winches", "Cable Drum Trailers","Rollers","Cable Drum Lifting Jacks","Cable Locators","Reeling Machine",
        "Cable Pulling Grips & Swivel Link","Duct Rods", "Hydraulic Cutting and Crimping Tools",
        "Warning Tapes", "Manhole", "Ropes", "Duct","Electrical", "Solar",
        "Pipes","Optical Fibre Cables","Optical Fiber Connectors" ,"Optical Fiber Adapters",
        "Optical Fiber Consumable","Optical Fiber Instruments", "Optical Distribution Frames",
        "Optical Fiber Patch Cord","Optical Fiber Tools","Cabinets","Cable Joint Products",
        "Cable & Wires", "Connectors", "Distribution Boxes", "Ducts Accessories",
        "Manhole Accessories", "Marking & Protection","Earthing Hardware","Miscellaneous", "Poles & Accessories",
        "Tapes","Terminal Blocks","Test & Measurement","Telecom Tools","Other Products"]
    products_by_cat = {cat: [] for cat in all_categories}
    for product in products_list:
        cat = product["category"] if product["category"] in all_categories else "Other Products"
        products_by_cat[cat].append(product)
    return render_template(
        "dashboard.html",
        products_by_cat=products_by_cat,all_categories=all_categories
    )
@app.route("/admin/client-orders")
def client_orders():
    if not session.get("authenticated") or not session.get("is_admin"):
        flash("Admin access required")
        return redirect(url_for("login"))
    if not session.get("is_admin") or not is_admin_in_db(session.get("user_email")):
        flash("Admin access required")
        return redirect(url_for("dashboard"))
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders ORDER BY datetime(last_updated) DESC")
    orders = cursor.fetchall()
    conn.close()
    return render_template("admin.html", section="client-orders", orders=orders)



@app.route("/admin/delete-order/<int:order_id>", methods=["POST"])
def delete_order(order_id):
    if not session.get("authenticated") or not session.get("is_admin"):
        flash("Admin access required")
        return redirect(url_for("login"))

    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    conn.commit()
    conn.close()

    flash("Order deleted successfully!", "success")
    return redirect(url_for("client_orders"))

@app.route("/my-orders")
def my_orders():
    if not session.get("authenticated"):
        flash("Please login to view your orders", "warning")
        return redirect(url_for("login"))

    user_email = session.get("user_email")
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM orders WHERE user_email = ? ORDER BY created_at DESC",
        (user_email,)
    )
    orders = cursor.fetchall()
    conn.close()

    return render_template("my_orders.html", orders=orders)

@app.route("/admin/send-quotation", methods=["POST"])
def send_quotation():
    if not session.get("authenticated") or not session.get("is_admin"):
        flash("Admin access required", "danger")
        return redirect(url_for("login"))
    order_id = request.form.get("id","").strip()
    client_email = request.form.get("user_email","").strip()
    message_body = request.form["message"]
    attachment = request.files.get("attachment")
    print("=== FORM DEBUG ===")
    print("Request method:", request.method)
    print("All form data:", dict(request.form))
    print("All form keys:", list(request.form.keys()))
    print("=== END DEBUG ===")
    if not client_email or not order_id:
        flash("Missing recipient information.", "danger")
        return redirect(url_for("client_orders"))
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT product_name, quantity FROM orders WHERE id = ?", (order_id,))
    order_row = cursor.fetchone()
    order_name = order_row[0] if order_row else None
    order_quantity = order_row[1] if order_row else None
    conn.close()
    email_user = os.getenv("SENDER_GMAIL_ADDRS")
    email_pass = os.getenv("GMAIL_APP_PASSWORD_1")
    msg = MIMEMultipart()
    msg["Subject"] = f"Quotation for Order #{order_id}: {order_name} ({order_quantity})"
    msg["From"] = email_user
    msg["To"] = client_email
    msg.attach(MIMEText(message_body, "plain"))
    attachment_name = None
    if attachment and attachment.filename:
        attachment_name = attachment.filename
        filepath = os.path.join(UPLOAD_FOLDER, attachment_name)
        attachment.save(filepath)
        file_data = open(filepath, "rb").read()
        part = MIMEApplication(file_data, Name=attachment_name)
        part["Content-Disposition"] = f'attachment; filename="{attachment_name}"'
        msg.attach(part)
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(email_user, email_pass)
            server.send_message(msg)
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        cursor.execute("""
                    INSERT INTO messages (order_id, user_email, subject, body, attachment_name,
                    order_name, order_quantity)
                    VALUES (?, ?, ?, ?, ?,?,?)
                """, (
            order_id,
            client_email,
            f"Quotation for Order #{order_id}",
            message_body,
            attachment.filename if attachment and attachment.filename else None,
            order_name,
            order_quantity
        ))
        cursor.execute("""
                    UPDATE orders 
                    SET status = 'quote sent', last_updated = datetime('now', '+4 hours') 
                    WHERE id = ?
                """, (order_id,))
        conn.commit()
        conn.close()
        flash(f"Quotation sent to {client_email}", "success")
    except Exception as e:
        app.logger.error("Error sending quotation: %s", e)
        flash("Failed to send quotation. Check server logs.", "danger")
    return redirect(url_for("client_orders"))



@app.route("/admin/dispatch-order/<int:order_id>", methods=["POST"])
def dispatch_order(order_id):
    if not session.get("authenticated") or not session.get("is_admin"):
        flash("Admin access required", "danger")
        return redirect(url_for("login"))
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("""
            SELECT id, user_email, product_name, quantity
            FROM orders 
            WHERE id = ?
        """, (order_id,))
    order = cursor.fetchone()
    conn.close()

    if not order:
        flash("Order not found", "danger")
        return redirect(url_for("admin_dashboard"))
    order_id = order[0]
    client_email = order[1]
    product_name = order[2]
    quantity = order[3]

    email_user = os.getenv("SENDER_GMAIL_ADDRS")
    email_pass = os.getenv("GMAIL_APP_PASSWORD_1")

    msg = MIMEMultipart()
    msg["Subject"] = f"Order Dispatched - Order #{order_id}: {product_name}"
    msg["From"] = email_user
    msg["To"] = client_email
    email_body = f"""Dear Customer,

    Good news! Your order has been dispatched.

    Order Details:
    - Order ID: #{order_id}
    - Product: {product_name}
    - Quantity: {quantity}
    Your order is on its way and should arrive by the expected delivery date.
    If you have any questions, please don't hesitate to contact us.
    Best regards,
    Elfit Arabia Team"""
    msg.attach(MIMEText(email_body, "plain"))
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(email_user, email_pass)
            server.send_message(msg)
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        cursor.execute("""
                INSERT INTO messages (order_id, user_email, subject, body, attachment_name,
                order_name, order_quantity, is_read)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """, (
            order_id,
            client_email,
            f"Order Dispatched - Order #{order_id}",
            email_body,
            None,  # No attachment for dispatch notification
            product_name,
            quantity
        ))
        # Update order status to "dispatched"
        cursor.execute("""
                UPDATE orders 
                SET status = 'dispatched' ,  last_updated = datetime('now', '+4 hours')
                WHERE id = ?
            """, (order_id,))

        conn.commit()
        conn.close()

        flash(f"Dispatch notification sent to {client_email} and status updated to 'Dispatched'", "success")
    except Exception as e:
        app.logger.error("Error sending dispatch notification: %s", e)
        flash("Failed to send dispatch notification. Check server logs.", "danger")
    return redirect(url_for("client_orders"))


@app.route("/admin/mark-delivered/<int:order_id>", methods=["POST"])
def mark_delivered(order_id):
    if not session.get("authenticated") or not session.get("is_admin"):
        flash("Admin access required", "danger")
        return redirect(url_for("login"))

    # Get order details from database
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, user_email, product_name, quantity
        FROM orders 
        WHERE id = ?
    """, (order_id,))
    order = cursor.fetchone()
    conn.close()

    if not order:
        flash("Order not found", "danger")
        return redirect(url_for("admin_dashboard"))

    order_id = order[0]
    client_email = order[1]
    product_name = order[2]
    quantity = order[3]
    # Prepare email
    email_user = os.getenv("SENDER_GMAIL_ADDRS")
    email_pass = os.getenv("GMAIL_APP_PASSWORD_1")
    msg = MIMEMultipart()
    msg["Subject"] = f"Order Delivered - Order #{order_id}: {product_name}"
    msg["From"] = email_user
    msg["To"] = client_email
    # Email body
    email_body = f"""Dear Customer,

Your order has been successfully delivered!\n

Order Details:\n
- Order ID: #{order_id}\n
- Product: {product_name}\n
- Quantity: {quantity}\n
Thank you for choosing Elfit Arabia. We hope you're satisfied with your purchase.\n
If you have any questions or concerns about your order, please don't hesitate to contact us.\n
Best regards,\n
Elfit Arabia Team\n"""

    msg.attach(MIMEText(email_body, "plain"))

    try:
        # Send email
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(email_user, email_pass)
            server.send_message(msg)

        # Save to messages table and update order status
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()

        # Insert delivery confirmation message
        cursor.execute("""
            INSERT INTO messages (order_id, user_email, subject, body, attachment_name,
            order_name, order_quantity, is_read)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            order_id,
            client_email,
            f"Order Delivered - Order #{order_id}",
            email_body,
            None,  # No attachment for delivery notification
            product_name,
            quantity
        ))
        # Update order status to "delivered"
        cursor.execute("""
            UPDATE orders 
            SET status = 'delivered' ,  last_updated = datetime('now', '+4 hours')
            WHERE id = ?
        """, (order_id,))
        conn.commit()
        conn.close()
        flash(f"Delivery confirmation sent to {client_email} and status updated to 'Delivered'", "success")
    except Exception as e:
        app.logger.error("Error sending delivery confirmation: %s", e)
        flash("Failed to send delivery confirmation. Check server logs.", "danger")
    return redirect(url_for("client_orders"))

@app.route("/cancel-order/<int:message_id>", methods=["POST"])
def cancel_order(message_id):
    if not session.get("authenticated"):
        flash("Please login first", "warning")
        return redirect(url_for("login"))
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    # Get message details
    cursor.execute("SELECT * FROM messages WHERE id=?", (message_id,))
    msg = cursor.fetchone()
    if not msg:
        flash("Message not found", "danger")
        conn.close()
        return redirect(url_for("my_messages"))
    user_email = msg["user_email"]
    order_name = msg["order_name"]
    order_quantity = msg["order_quantity"]
    # Insert cancellation message in messages
    cursor.execute("""
        INSERT INTO messages (user_email, subject, body, order_name, order_quantity, created_at, is_read)
        VALUES (?, ?, ?, ?, ?, datetime('now'), 0)
    """, (
        user_email,
        "Order Cancelled",
        f"Your order '{order_name}' ({order_quantity}) has been cancelled by the client.",
        order_name,
        order_quantity
    ))
    cursor.execute("""
        UPDATE orders 
        SET status = 'cancelled' , last_updated = datetime('now', '+4 hours')
        WHERE product_name = ? AND user_email = ?
    """, (order_name, user_email))
    conn.commit()
    conn.close()
    flash("Order has been cancelled", "success")
    return redirect(url_for("my_messages"))




@app.route("/my-messages")
def my_messages():
    if not session.get("authenticated"):
        flash("Please login to access messages", "warning")
        return redirect(url_for("login"))

    user_email = session.get("user_email")
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT m.id, m.order_id, m.subject, m.body, m.attachment_name,
               m.order_name, m.order_quantity, m.created_at, m.is_read,
               o.status AS order_status
        FROM messages m
        LEFT JOIN orders o
          ON m.order_name = o.product_name
         AND m.user_email = o.user_email
        WHERE m.user_email = ?
        ORDER BY m.created_at DESC
    """, (user_email,))
    messages = cursor.fetchall()
    cursor.execute("""
            UPDATE messages SET is_read = 1 
            WHERE user_email = ? AND (is_read = 0 OR is_read IS NULL)
        """, (user_email,))
    conn.commit()
    conn.close()
    return render_template("my_messages.html", messages=messages)

@app.route("/place-order/<int:message_id>")
def finalize_order(message_id):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM messages WHERE id=?", (message_id,))
    msg = cursor.fetchone()
    if msg:
        user_email=msg[2]
        quotation=msg[4]
        order_name=msg[6]
        order_quantity=msg[7]
        order_id=msg[1]
        try:
            msg_out = MIMEMultipart()
            msg_out["From"] = os.getenv('SENDER_GMAIL_ADDRS')
            msg_out["To"] = os.getenv('ADMIN_EMAIL')
            msg_out["Subject"] = f"New Order Confirmation - {order_name} ({order_quantity})"
            text=f"""
Hello Admin,

Client {user_email} has confirmed an order.
The Order: {order_name} 
Order Quantity: {order_quantity} 
The Quotation sent: '{quotation}'
Placed at: {datetime.now()}
- Elfit Arabia System          
"""
            msg_out.attach(MIMEText(text, "plain"))
            with smtplib.SMTP("smtp.gmail.com", 587) as server:
                server.starttls()
                server.login(os.getenv("SENDER_GMAIL_ADDRS"), os.getenv("GMAIL_APP_PASSWORD_1"))
                server.sendmail(os.getenv("SENDER_GMAIL_ADDRS"), os.getenv("ADMIN_EMAIL"), msg_out.as_string())
            cursor.execute("""
                            UPDATE orders 
                            SET status = ? ,  last_updated = datetime('now', '+4 hours')
                            WHERE product_name = ? AND user_email = ?
                        """, ("order placed", order_name, user_email))
            subject = f"Place Order Confirmed - Order #{order_id if order_id else 'N/A'}"
            body = f"Your order '{order_name}' ({order_quantity}) has been confirmed successfully."
            cursor.execute("""
                            INSERT INTO messages (order_id, subject, body, attachment_name, order_name, order_quantity, user_email, created_at, is_read)
                            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), 0)
                        """, (
                order_id,
                subject,
                body,
                None,  # no attachment
                order_name,
                order_quantity,
                user_email
            ))
            conn.commit()
        except Exception as e:
            flash(f"Email failed to send: {str(e)}")
    else:
        flash("order does not exist anymore")
    return redirect(url_for("my_messages"))

@app.route("/contact-us")
def contact_us():
    admin_whatsapp = os.getenv("ADMIN_WHATSAPP")
    wa_link = f"https://web.whatsapp.com/send?phone={admin_whatsapp}"
    return redirect(wa_link)

@app.route("/talk-further/<int:message_id>")
def talk_further(message_id):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM messages WHERE id=?", (message_id,))
    msg = cursor.fetchone()
    if not msg:
        flash("Message not found")
        return redirect(url_for("my_messages"))
    user_email = msg[2]
    order_id=msg[1]
    order_name = msg[6]
    order_quantity = msg[7]
    admin_whatsapp = os.getenv("ADMIN_WHATSAPP")
    conn.close()
    text = f"""Hello Admin, I want to discuss further about my PLACED ORDER.\n\n"
           Order ID: {order_id}\n
           Order: {order_name} ({order_quantity})\n" 
           f"My Email: {user_email}"""
    encoded_text = quote(text)
    wa_link = f"https://web.whatsapp.com/send?phone={admin_whatsapp}&text={encoded_text}"
    return redirect(wa_link)
@app.route("/register", methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        client_name = request.form.get('client_name', '').strip()
        phone = request.form.get('phone', '').strip()
        address = request.form.get('address', '').strip()
        company = request.form.get('company', '').strip()
        errors = []
        if not email:
            errors.append("Email is required")
        elif not validate_email(email):
            errors.append("Please enter a valid email address")
        if not client_name:
            errors.append("Full name is required")
        elif len(client_name) < 2:
            errors.append("Name must be at least 2 characters long")
        if not phone:
            errors.append("Phone number is required")
        elif not validate_phone(phone):
            errors.append("Please enter a valid phone number") # If there are validation errors, show them
        if errors:
            for error in errors:
                flash(error, 'error')
            return render_template("register.html") # Try to add the client to the database
        if add_client(email, client_name, phone, address, company):
            flash("Registration successful! You can now log in.", 'success')
            return redirect(url_for('login'))  # Redirect to login page
        else:
            flash("An account with this email already exists. Please try logging in instead.", 'error')
            return render_template("register.html")
    return render_template("register.html")


@app.route("/api/orders/inquired")
def get_inquired_items():
    """API endpoint to get ALL items that have ever been ordered with counts"""
    if not session.get("authenticated") or not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        conn = sqlite3.connect(DATABASE)
        cur = conn.cursor()

        # Get ALL products that have ever been ordered, regardless of status
        query = """
                SELECT product_name, COUNT(*) as order_count
                FROM orders
                GROUP BY product_name
                ORDER BY order_count DESC, product_name ASC \
                """

        cur.execute(query)
        rows = cur.fetchall()
        conn.close()

        # Format results
        items = []
        for row in rows:
            items.append({
                "product_name": row[0],
                "order_count": row[1]
            })

        return jsonify(items)

    except Exception as e:
        print(f"❌ Error in get_inquired_items: {e}")
        return jsonify({"error": str(e)}), 500




@app.route("/api/dashboard-data")
def dashboard_data():
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    # UAE timezone
    now_uae = datetime.now(ZoneInfo("Asia/Dubai"))
    one_week_ago = now_uae - timedelta(days=7)
    # --- Metrics ---
    cur.execute("SELECT COUNT(*) FROM orders WHERE datetime(created_at) >= ?", (one_week_ago,))
    orders_this_week = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM orders WHERE status IN ('inquiry placed', 'quote sent')")
    unplaced_orders = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'delivered'")
    delivered_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM products WHERE stock_status = 'in_stock'")
    in_stock_products = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM users")
    total_clients = cur.fetchone()[0]

    return jsonify({
        "orders_this_week": orders_this_week,
        "delivered_count": delivered_count,
        "in_stock_products": in_stock_products,
        "total_clients": total_clients,"unplaced_orders": unplaced_orders
    })




@app.route("/api/orders/<category>")
def get_orders_by_category(category):
    """API endpoint to get orders by category"""
    if not session.get("authenticated") or not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        conn = sqlite3.connect(DATABASE)
        cur = conn.cursor()

        now_uae = datetime.now(ZoneInfo("Asia/Dubai"))
        one_week_ago = (now_uae - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

        # Define queries based on category
        if category == "week":
            query = """
                    SELECT id, \
                           product_name, \
                           user_email, \
                           quantity, \
                           expected_date,
                           status, \
                           last_updated, \
                           comments, \
                           created_at
                    FROM orders
                    WHERE datetime(last_updated) >= ?
                    ORDER BY datetime(last_updated) DESC \
                    """
            params = (one_week_ago,)

        elif category == "delivered":
            query = """
                    SELECT id, \
                           product_name, \
                           user_email, \
                           quantity, \
                           expected_date,
                           status, \
                           last_updated, \
                           comments, \
                           created_at
                    FROM orders
                    WHERE status = 'delivered'
                    ORDER BY datetime(last_updated) DESC \
                    """
            params = ()

        elif category == "pending":
            query= """
                        SELECT id, \
                           product_name, \
                           user_email, \
                           quantity, \
                           expected_date,
                           status, \
                           last_updated, \
                           comments, \
                           created_at
                        FROM orders
                        WHERE status IN ('dispatched', 'order placed')
                        ORDER BY last_updated DESC
                        """
            params = ()


        elif category == "dispatched":
            query = """
                    SELECT id, \
                           product_name, \
                           user_email, \
                           quantity, \
                           expected_date,
                           status, \
                           last_updated, \
                           comments, \
                           created_at
                    FROM orders
                    WHERE status = 'dispatched'
                    ORDER BY datetime(last_updated) DESC \
                    """
            params = ()

        elif category == "unplaced":
            query = """
                    SELECT id,
                           product_name,
                           user_email,
                           quantity,
                           expected_date,
                           status,
                           last_updated,
                           comments,
                           created_at
                    FROM orders
                    WHERE status IN ('inquiry received', 'quote sent')
                    ORDER BY last_updated DESC \
                    """
            params = ()


        else:
            return jsonify({"error": "Invalid category"}), 400

        # Execute query
        cur.execute(query, params)
        rows = cur.fetchall()
        conn.close()

        # Format results
        orders = []
        for row in rows:
            orders.append({
                "id": row[0],
                "product_name": row[1],
                "client_email": row[2],  # user_email from database
                "quantity": row[3],
                "expected_date": row[4],
                "order_status": row[5],
                "last_updated": row[6],
                "comments": row[7] if row[7] else "",
                "created_at": row[8]
            })

        return jsonify(orders)

    except Exception as e:
        print(f"❌ Error in get_orders_by_category: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/delivered-by-category")
def delivered_by_category():
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()

    # 1 — Get delivered orders joined with product category
    cur.execute("""
        SELECT 
            o.id,
            o.product_name,
            o.user_email,
            o.last_updated,
            p.category
        FROM orders o
        JOIN products p ON LOWER(o.product_name) = LOWER(p.product_name)
        WHERE o.status = 'delivered'
        ORDER BY p.category, o.last_updated DESC
    """)

    rows = cur.fetchall()
    conn.close()

    categories = {}

    # Organize by category
    for row in rows:
        order_id, product, email, delivered_at, category = row

        if category not in categories:
            categories[category] = []

        categories[category].append({
            "id": order_id,
            "product": product,
            "client": email,
            "delivered_at": delivered_at
        })

    return jsonify(categories)






@app.route("/api/orders/clients")
def get_client_orders():
    """API endpoint to get all clients with their order details and product breakdown"""
    if not session.get("authenticated") or not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        conn = sqlite3.connect(DATABASE)
        cur = conn.cursor()

        # Get all clients with their total order counts
        cur.execute("""
            SELECT user_email, COUNT(*) as total_orders
            FROM orders
            GROUP BY user_email
            ORDER BY total_orders DESC, user_email ASC
        """)
        clients = cur.fetchall()

        # For each client, get their product breakdown
        client_data = []
        for client in clients:
            email = client[0]
            total_orders = client[1]

            # Get product breakdown for this client
            cur.execute("""
                SELECT product_name, COUNT(*) as product_count
                FROM orders
                WHERE user_email = ?
                GROUP BY product_name
                ORDER BY product_count DESC
            """, (email,))
            products = cur.fetchall()

            product_breakdown = [
                {"product_name": p[0], "count": p[1]}
                for p in products
            ]

            client_data.append({
                "email": email,
                "total_orders": total_orders,
                "products": product_breakdown
            })

        conn.close()
        return jsonify(client_data)

    except Exception as e:
        print(f"❌ Error in get_client_orders: {e}")
        return jsonify({"error": str(e)}), 500



@app.route("/api/timeline/months")
def get_timeline_months():
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT strftime('%Y-%m', created_at) AS ym
        FROM orders
        WHERE created_at >= '2025-10-01'
        ORDER BY ym DESC
    """)

    months = [row[0] for row in cur.fetchall()]
    conn.close()

    return jsonify(months)

@app.route("/api/timeline/orders/<month>")
def get_timeline_orders(month):
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, product_name, user_email, status, created_at
        FROM orders
        WHERE strftime('%Y-%m', created_at) = ?
        ORDER BY created_at DESC
    """, (month,))

    rows = cur.fetchall()
    conn.close()

    results = [
        {
            "id": r[0],
            "product_name": r[1],
            "user_email": r[2],
            "status": r[3],
            "created_at": r[4]
        }
        for r in rows
    ]

    return jsonify(results)


@app.route("/api/payment-status")
def payment_status_api():
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, product_name, user_email, quantity, status,
               payment_status, created_at, last_updated
        FROM orders
        WHERE status IN ('order placed', 'dispatched', 'delivered')
        ORDER BY last_updated DESC
    """)
    rows = cur.fetchall()
    conn.close()

    results = []
    for r in rows:
        results.append({
            "id": r[0],
            "product_name": r[1],
            "user_email": r[2],
            "quantity": r[3],
            "status": r[4],
            "payment_status": r[5] or "unpaid",
            "created_at": r[6],
            "last_updated": r[7]
        })

    return jsonify(results)

@app.route("/api/payment-status/update/<int:order_id>", methods=["POST"])
def update_payment_status(order_id):
    data = request.get_json()
    new_status = data.get("payment_status", "unpaid")

    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute("""
        UPDATE orders SET payment_status = ?
        WHERE id = ?
    """, (new_status, order_id))
    conn.commit()
    conn.close()

    return jsonify({"success": True})




def get_unread_message_count(user_email):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*) FROM messages 
        WHERE user_email = ? AND (is_read = 0 OR is_read IS NULL)
    """, (user_email,))
    count = cursor.fetchone()[0]
    conn.close()
    return count
@app.route("/logout")
def logout():
    """Logout route - clears session"""
    session.clear()
    flash("You have been logged out successfully")
    return redirect(url_for("login"))

if __name__ == "__main__":
    init_db()
    app.run(debug=True)