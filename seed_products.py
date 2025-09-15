import sqlite3, json
from app import DATABASE

with open("product_data.json", "r") as f:
    products = json.load(f)

conn = sqlite3.connect(DATABASE)
cursor = conn.cursor()

# Ensure products table exists
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

# Wipe existing products (optional: so catalog always matches products.json)
cursor.execute("DELETE FROM products")

# Insert products
for p in products:
    cursor.execute("""
        INSERT INTO products (id, product_name, category, product_options, product_rate, stock_status, image_filename)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        p.get("id"),
        p["name"],
        p["category"],
        json.dumps(p.get("options", {})),
        p.get("rate", ""),
        p.get("stock", "in_stock"),
        p.get("image", "")
    ))
conn.commit()
conn.close()
print("✅ Products loaded from products.json")