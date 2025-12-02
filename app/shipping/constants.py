"""
Shipping Method Constants
Canonical list of shipping methods available in the system.
"""

SHIPPING_METHODS = [
    {
        "id": "express",
        "label": "DHL Express (Fast)",
        "short_label": "Express (Fast)",
        "description": "Fastest delivery. Fully tracked from China to your location.",
        "guarantee": "Delivery: 3–7 days",
        "notes": [
            "Best for urgent or valuable items",
            "Doorstep delivery",
            "Requires valid phone number",
            "Higher cost"
        ],
        "color": "red",
        "icon": "🚀"
    },
    {
        "id": "economy_plus",
        "label": "DHL eCommerce (Standard)",
        "short_label": "eCommerce (Standard)",
        "description": "Reliable shipping with tracking. Delivered by DHL partner or Post Office in your country.",
        "guarantee": "Delivery: 10–20 days",
        "notes": [
            "Cheaper than express",
            "Tracking available",
            "Final delivery by Post Office or DHL partner"
        ],
        "color": "yellow",
        "icon": "📦"
    },
    {
        "id": "economy",
        "label": "Economy Mail (PO Pickup)",
        "short_label": "Economy Mail (PO Pickup)",
        "description": "Low-cost shipping. Parcel will be sent to your local Post Office for pickup.",
        "guarantee": "Delivery: 20–60 days",
        "notes": [
            "Cheapest option",
            "Pickup at Post Office",
            "Tracking may be limited",
            "Good for small items (0–2 kg)",
            "Post Office will call/SMS when it arrives"
        ],
        "color": "green",
        "icon": "📮"
    }
]

# Helper functions
def get_shipping_method(method_id: str) -> dict:
    """Get shipping method by ID."""
    for method in SHIPPING_METHODS:
        if method["id"] == method_id:
            return method
    return None

def get_all_shipping_methods() -> list:
    """Get all shipping methods."""
    return SHIPPING_METHODS

def get_shipping_method_ids() -> list:
    """Get list of all shipping method IDs."""
    return [method["id"] for method in SHIPPING_METHODS]

def is_valid_shipping_method(method_id: str) -> bool:
    """Check if a shipping method ID is valid."""
    return method_id in get_shipping_method_ids()

