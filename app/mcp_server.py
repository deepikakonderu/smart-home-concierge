import os
import json
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("SmartHomeConcierge")

# Simple in-memory database for demo purposes (can persist to a json file in local workspace)
DB_FILE = os.path.join(os.path.dirname(__file__), "smart_home_db.json")

def load_db():
    if not os.path.exists(DB_FILE):
        default_db = {
            "inventory": {
                "milk": {"quantity": 1, "unit": "gallon", "low_threshold": 1},
                "eggs": {"quantity": 12, "unit": "pcs", "low_threshold": 6},
                "bread": {"quantity": 0, "unit": "loaf", "low_threshold": 1},
                "detergent": {"quantity": 2, "unit": "bottles", "low_threshold": 1},
                "toilet_paper": {"quantity": 4, "unit": "rolls", "low_threshold": 8}
            },
            "maintenance": [
                {"id": 1, "task": "HVAC Filter Replacement", "status": "Pending", "due_date": "2026-07-01", "estimated_cost": 45.0},
                {"id": 2, "task": "Gutter Cleaning", "status": "Completed", "due_date": "2026-06-15", "estimated_cost": 150.0},
                {"id": 3, "task": "Kitchen Sink Leak Repair", "status": "Pending", "due_date": "2026-06-25", "estimated_cost": 120.0}
            ]
        }
        with open(DB_FILE, "w") as f:
            json.dump(default_db, f, indent=2)
        return default_db
    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_db(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2)

@mcp.tool()
def get_inventory() -> str:
    """Get the current home inventory and grocery stock levels."""
    db = load_db()
    return json.dumps(db.get("inventory", {}), indent=2)

@mcp.tool()
def update_inventory(item: str, quantity: int, unit: str = "pcs", low_threshold: int = 1) -> str:
    """Add or update an item in the home inventory.
    
    Args:
        item: The name of the item (e.g. 'milk', 'detergent').
        quantity: The current count or quantity.
        unit: The unit of measurement (e.g. 'gallon', 'pcs').
        low_threshold: The quantity threshold below which a warning should trigger.
    """
    db = load_db()
    inventory = db.setdefault("inventory", {})
    item_lower = item.lower()
    inventory[item_lower] = {
        "quantity": quantity,
        "unit": unit,
        "low_threshold": low_threshold
    }
    save_db(db)
    return f"Updated {item} quantity to {quantity} {unit}."

@mcp.tool()
def get_maintenance_tasks() -> str:
    """Get the list of all home maintenance tasks, statuses, and costs."""
    db = load_db()
    return json.dumps(db.get("maintenance", []), indent=2)

@mcp.tool()
def schedule_maintenance(task: str, due_date: str, estimated_cost: float) -> str:
    """Schedule a new home maintenance task.
    
    Args:
        task: Description of the maintenance chore (e.g. 'AC checkup', 'Roof inspection').
        due_date: Due date formatted as YYYY-MM-DD.
        estimated_cost: The expected cost in dollars.
    """
    db = load_db()
    tasks = db.setdefault("maintenance", [])
    new_id = max([t["id"] for t in tasks], default=0) + 1
    new_task = {
        "id": new_id,
        "task": task,
        "status": "Pending",
        "due_date": due_date,
        "estimated_cost": estimated_cost
    }
    tasks.append(new_task)
    save_db(db)
    return f"Successfully scheduled task '{task}' with ID {new_id}."

@mcp.tool()
def verify_owner_pin(pin: str) -> str:
    """Verify if the owner PIN is correct. Required for secure operations like door locking or security updates.
    
    Args:
        pin: The 4-digit security PIN provided by the user.
    """
    if pin == "1234":
        return json.dumps({"verified": True, "message": "PIN verified successfully. Access granted."})
    return json.dumps({"verified": False, "message": "Incorrect PIN. Access denied."})

if __name__ == "__main__":
    mcp.run("stdio")
