"""
Email Processor Phase 4 - Status Detection Updates
Adds T-O-T-M and Hold status recognition
"""

# Status mapping with email triggers
STATUS_TRIGGERS = {
    'Active': [
        'back on market',
        'back on the market',
        'active',
        'new listing',
        'just listed'
    ],
    'Price Reduction': [
        'price reduction',
        'price reduced',
        'price drop',
        'reduced price',
        'price change',
        'new list price'
    ],
    'Highest & Best': [
        'highest and best',
        'highest & best',
        'multiple offer',
        'best and final',
        'h&b'
    ],
    'Pending': [
        'pending',
        'under contract',
        'in contract',
        'accepted offer'
    ],
    'Sold': [
        'sold',
        'closed',
        'sale complete'
    ],
    'T-O-T-M': [
        'temporarily off',
        'temporarily off the market',
        'temp off',
        'totm',
        't-o-t-m',
        'temporarily withdrawn'
    ],
    'Hold': [
        'on hold',
        'listing hold',
        'hold status',
        'status: hold',
        'placing hold'
    ]
}

def detect_status_from_email(subject, body):
    """
    Detect property status from email subject and body
    Returns the detected status or 'Active' as default
    """
    # Combine subject and body for searching
    search_text = (subject + ' ' + body).lower()
    
    # Priority order for status detection
    # (More specific statuses checked first)
    priority_order = [
        'Sold',
        'Pending', 
        'Highest & Best',
        'Hold',
        'T-O-T-M',
        'Price Reduction',
        'Active'
    ]
    
    for status in priority_order:
        triggers = STATUS_TRIGGERS.get(status, [])
        for trigger in triggers:
            if trigger.lower() in search_text:
                return status
    
    # Default to Active if no status detected
    return 'Active'

def get_status_color(status):
    """Return CSS color for status badge"""
    colors = {
        'Active': '#10b981',           # Green
        'Price Reduction': '#f59e0b',   # Amber
        'Highest & Best': '#dc2626',    # Red
        'Pending': '#8b5cf6',           # Purple
        'Sold': '#6b7280',              # Gray
        'T-O-T-M': '#f97316',           # Orange
        'Hold': '#a855f7'               # Purple (lighter)
    }
    return colors.get(status, '#6b7280')

def get_status_class(status):
    """Return CSS class for status badge"""
    # Convert status to CSS-friendly class name
    return 'status-' + status.lower().replace(' ', '-').replace('&', 'and')

# Example usage in email processor:
"""
def process_email(email_subject, email_body):
    # Detect status
    detected_status = detect_status_from_email(email_subject, email_body)
    
    # Extract property data using Claude...
    property_data = extract_property_data(email_subject, email_body)
    
    # Add status to property data
    property_data['status'] = detected_status
    
    # Save to database
    save_property(property_data)
"""

# Status descriptions for UI tooltips
STATUS_DESCRIPTIONS = {
    'Active': 'Property is actively listed and available',
    'Price Reduction': 'List price has been reduced',
    'Highest & Best': 'Multiple offers - requesting highest and best',
    'Pending': 'Offer accepted, awaiting closing',
    'Sold': 'Sale completed and closed',
    'T-O-T-M': 'Temporarily off the market (not cancelled)',
    'Hold': 'On hold due to seller, legal, or access issues'
}

# Statuses that should appear in "Active" filter results
ACTIVE_STATUSES = ['Active']

# Statuses that should be searchable
SEARCHABLE_STATUSES = [
    'Active',
    'Price Reduction', 
    'Highest & Best',
    'Pending',
    'Sold',
    'T-O-T-M',
    'Hold'
]

# Statuses available for internal/agent view
INTERNAL_STATUSES = ['Hold', 'T-O-T-M']
