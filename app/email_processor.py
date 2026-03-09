"""
AI-Powered Email Processor for Island Advantage Property System V4
Extracts comprehensive property data, status changes, flags, and compliance info

STATUS MAPPING (aligned with Phase 4.1 requirements):
- Available / New Listing → Active
- Auction Available → Auction Available  
- Price Reduced → do NOT set status to Price Reduced; set new_status to null and update price only
- 1st Accept → First Accepted
- Pending / Contract Executed → In Contract
- Back on Market → Active (ALWAYS resets to Active)
- Sold → Sold
- T-O-T-M / Temporarily Off the Market → TOTM
"""

import os
import json
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from anthropic import Anthropic
import re

class EmailProcessor:
    """Process property emails and extract structured data"""
    
    def __init__(self, anthropic_api_key: str):
        self.client = Anthropic(api_key=anthropic_api_key)
        self.model = "claude-sonnet-4-20250514"
    
    def process_email(self, email_data: Dict) -> Dict:
        """
        Process a single email and extract all property data
        
        Returns:
            {
                'property_data': {...},
                'status_change': {...},
                'flags': {...},
                'important_info': [...],
                'compliance_alerts': [...],
                'highest_best': {...},
                'attachments': [...]
            }
        """
        start_time = time.time()
        
        email_id = email_data.get('id', '')
        subject = email_data.get('subject', '')
        body = email_data.get('body', '')
        from_email = email_data.get('from', '')
        email_date = email_data.get('date', '')
        
        # Build comprehensive prompt
        prompt = self._build_extraction_prompt(subject, body, from_email, email_date)
        
        try:
            # Call Claude API
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}]
            )
            
            # Extract JSON from response
            extracted_data = self._parse_response(response.content[0].text)
            
            # Calculate processing time
            processing_time_ms = int((time.time() - start_time) * 1000)
            
            # Add metadata
            extracted_data['_metadata'] = {
                'email_id': email_id,
                'email_subject': subject,
                'email_from': from_email,
                'email_date': email_date,
                'processing_time_ms': processing_time_ms,
                'ai_model': self.model,
                'success': True
            }
            
            return extracted_data
            
        except Exception as e:
            return {
                '_metadata': {
                    'email_id': email_id,
                    'email_subject': subject,
                    'email_from': from_email,
                    'email_date': email_date,
                    'processing_time_ms': int((time.time() - start_time) * 1000),
                    'ai_model': self.model,
                    'success': False,
                    'error': str(e)
                }
            }
    
    def _build_extraction_prompt(self, subject: str, body: str, from_email: str, date: str) -> str:
        """Build comprehensive extraction prompt"""
        return f"""You are processing a real estate property email. Extract ALL relevant information into structured JSON format.

EMAIL DETAILS:
Subject: {subject}
From: {from_email}
Date: {date}

EMAIL BODY:
{body}

EXTRACTION REQUIREMENTS:

1. PROPERTY IDENTIFICATION (always extract if present):
   - MLS Number (critical)
   - Full Address (street, city, state, zip)
   - Property Type (SFR, 2-4 Unit, Condo, etc.)

2. PRICING:
   - Current List Price
   - Original List Price (if this is a price reduction)

3. STATUS DETERMINATION (pick ONE that best matches):
   **CRITICAL: Use ONLY these exact status values:**
   - "Active" - New listing / origination / available / back on market
   - "Auction Available" - Auction property / auction listing
   - NOTE: Do NOT use "Price Reduced" as a status. If email is a price reduction, set new_status=null and only update current_list_price.
   - "First Accepted" - Offer accepted / 1st accepted but not yet in contract
   - "In Contract" - Under contract / contract executed / pending
   - "Sold" - Closed successfully / sold
   - "Highest & Best" - Multiple offers received, highest and best deadline set
   - "TOTM" - Temporarily off the market (T-O-T-M)
   
   **IMPORTANT RULES:**
   - "Back on Market" emails should ALWAYS result in "Active" status
   - Any email indicating "Available" or "New Listing" = "Active"
   - "Auction" or "Auction Available" = "Auction Available"
   - "1st Accept" or "Offer Accepted" (without contract) = "First Accepted"
   - "Under Contract" or "Contract Executed" or "Pending" = "In Contract"
   - "Highest and Best" or "Highest & Best" or multiple offers with deadline = "Highest & Best"
   - "T-O-T-M" or "Temporarily Off the Market" or "TOTM" = "TOTM"

4. ACCESS & OCCUPANCY FLAGS (boolean true/false):
   - is_occupied: Property is occupied by tenant/owner
   - no_interior_access: Cannot show interior
   - no_open_houses: Open houses not allowed

5. FINANCING FLAGS (boolean true/false):
   - cash_only: Must be cash purchase
   - renovation_loan_ok: Renovation/rehab loans accepted
   - conventional_ok: Conventional financing allowed
   - hard_money_ok: Hard money loans allowed
   - hard_money_contingency: Any specific hard money conditions
   - fha_ok: FHA loans allowed
   - va_ok: VA loans allowed

6. HIGHEST & BEST DEADLINE (if multiple offers mentioned):
   - due_date: YYYY-MM-DD format
   - due_time: HH:MM format (24-hour)
   - offer_rules: How to structure offers
   - submission_instructions: Where/how to submit

7. IMPORTANT PROPERTY INFO (extract any critical details):
   Array of:
   - category: "Occupancy" / "Financing" / "Safety" / "Zoning" / "Offers" / "Deadlines"
   - title: Brief headline
   - content: Full details
   - severity: "info" / "warning" / "critical"

8. COMPLIANCE ALERTS (extract if mentioned):
   Array of:
   - alert_type: "Violation" / "ECB" / "Fine" / "CO Issue" / "Safety Hazard" / "Illegal Unit"
   - title: Brief description
   - description: Full details
   - severity: "low" / "medium" / "high" / "critical"

9. ATTACHMENTS (if email mentions attached documents):
   Array of:
   - filename: Document name
   - category: "CO/TCO" / "Violations" / "ECB" / "Fines" / "FOIL" / "Condition" / "Offers" / "Contracts" / "Closing"
   - priority: "Origination" / "Critical" / "Reference"

RESPONSE FORMAT (valid JSON only):
{{
  "property_data": {{
    "mls_number": "string or null",
    "address": "string or null",
    "city": "string or null",
    "zip_code": "string or null",
    "property_type": "string or null",
    "current_list_price": number or null,
    "original_list_price": number or null
  }},
  "status_change": {{
    "new_status": "string or null (MUST be one of: Active, Auction Available, First Accepted, In Contract, Sold, Highest & Best, TOTM — do NOT use Price Reduced)",
    "confidence": "high/medium/low",
    "reasoning": "why this status was chosen"
  }},
  "flags": {{
    "is_occupied": boolean,
    "no_interior_access": boolean,
    "no_open_houses": boolean,
    "cash_only": boolean,
    "renovation_loan_ok": boolean,
    "conventional_ok": boolean,
    "hard_money_ok": boolean,
    "hard_money_contingency": "string or null",
    "fha_ok": boolean,
    "va_ok": boolean
  }},
  "highest_best": {{
    "due_date": "YYYY-MM-DD or null",
    "due_time": "HH:MM or null",
    "offer_rules": "string or null",
    "submission_instructions": "string or null"
  }},
  "important_info": [
    {{
      "category": "string",
      "title": "string",
      "content": "string",
      "severity": "info/warning/critical"
    }}
  ],
  "compliance_alerts": [
    {{
      "alert_type": "string",
      "title": "string",
      "description": "string",
      "severity": "low/medium/high/critical"
    }}
  ],
  "attachments": [
    {{
      "filename": "string",
      "category": "string",
      "priority": "Origination/Critical/Reference"
    }}
  ]
}}

CRITICAL RULES:
- If information is not mentioned, use null (not empty string)
- Boolean flags default to false if not mentioned
- conventional_ok defaults to true unless explicitly restricted
- Only set status if you're confident (high/medium confidence)
- Extract pricing as numbers without $ or commas
- Be conservative with compliance alerts - only flag real issues
- **IMPORTANT: "Back on Market" ALWAYS results in "Active" status**

Return ONLY valid JSON, no additional text."""

    def _parse_response(self, response_text: str) -> Dict:
        """Parse AI response and extract JSON"""
        # Try to find JSON in response
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            return json.loads(json_str)
        else:
            # Attempt to parse entire response as JSON
            return json.loads(response_text)
    
    def determine_status_from_subject(self, subject: str) -> Optional[str]:
        """Quick status determination from subject line"""
        subject_lower = subject.lower()
        
        # Check for specific keywords (aligned with Phase 4.1 requirements)
        if any(word in subject_lower for word in ['back on market', 'bom', 'contract cancelled', 'fell through']):
            return 'Active'  # Back on Market always becomes Active
        elif any(word in subject_lower for word in ['new list price', 'new listing price']):
            return 'Active'  # NEW: New list price indicates property is available/active
        elif any(word in subject_lower for word in ['origination', 'new listing', 'just listed', 'available']):
            return 'Active'
        elif any(word in subject_lower for word in ['auction', 'auction available']):
            return 'Auction Available'
        elif any(word in subject_lower for word in ['price reduction', 'price change', 'reduced']):
            return None  # Price reduction: update price only, do NOT change status
        elif any(word in subject_lower for word in ['offer accepted', '1st accepted', 'accepted offer', '1st accept']):
            return 'First Accepted'
        elif any(word in subject_lower for word in ['under contract', 'contract executed', 'in contract', 'pending']):
            return 'In Contract'
        elif any(phrase in subject_lower for phrase in ['highest and best', 'highest & best', 'highest & best', 'h&b due', 'h and b']):
            return 'Highest & Best'
        elif any(word in subject_lower for word in ['closed', 'sold', 'closing']):
            return 'Sold'
        elif any(word in subject_lower for word in ['t-o-t-m', 'totm', 'temporarily off']):
            return 'TOTM'
        
        return None
    
    def extract_mls_from_text(self, text: str) -> Optional[str]:
        """Extract MLS number from text using regex patterns"""
        # Common MLS patterns
        patterns = [
            r'MLS#?\s*:?\s*(\d{6,8})',
            r'MLS\s+Number:?\s*(\d{6,8})',
            r'ID#?\s*:?\s*(\d{6,8})',
            r'Listing\s+#:?\s*(\d{6,8})',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return None
    
    def validate_extracted_data(self, data: Dict) -> Tuple[bool, List[str]]:
        """Validate extracted data for completeness and accuracy"""
        errors = []
        
        # Must have either MLS or address
        if not data.get('property_data', {}).get('mls_number') and \
           not data.get('property_data', {}).get('address'):
            errors.append("Missing both MLS number and address")
        
        # If status change, must have a valid status
        if data.get('status_change'):
            status = data['status_change'].get('new_status')
            valid_statuses = ['Active', 'Auction Available', 'First Accepted', 'In Contract', 'Sold', 'Highest & Best', 'TOTM']
            if status and status not in valid_statuses:
                errors.append(f"Invalid status: {status}. Must be one of: {', '.join(valid_statuses)}")
        
        # Price must be positive if present
        price = data.get('property_data', {}).get('current_list_price')
        if price and price <= 0:
            errors.append("Invalid price (must be positive)")
        
        return len(errors) == 0, errors


class StatusFlowValidator:
    """Validate status transitions follow business rules"""
    
    VALID_TRANSITIONS = {
        'Active': ['First Accepted', 'In Contract', 'Sold', 'Auction Available', 'Highest & Best'],
        'Auction Available': ['First Accepted', 'In Contract', 'Active', 'Sold', 'Highest & Best'],
        # Price Reduced status removed - price reductions keep current status
        'First Accepted': ['In Contract', 'Active'],
        'In Contract': ['Sold', 'Active'],
        'Sold': [],  # Final status, no transitions
        'Highest & Best': ['First Accepted', 'In Contract', 'Active', 'Sold'],
        'TOTM': ['Active', 'First Accepted', 'Sold'],  # Can come back on market or sell
    }
    
    @classmethod
    def is_valid_transition(cls, old_status: str, new_status: str) -> bool:
        """Check if status transition is valid"""
        if not old_status:
            return True  # First status always valid
        
        if old_status not in cls.VALID_TRANSITIONS:
            return False
        
        # Back on Market always allows transition to Active
        if new_status == 'Active':
            return True
        
        # Any status can transition to TOTM
        if new_status == 'TOTM':
            return True
        
        return new_status in cls.VALID_TRANSITIONS[old_status]
    
    @classmethod
    def get_valid_next_statuses(cls, current_status: str) -> List[str]:
        """Get list of valid next statuses"""
        statuses = cls.VALID_TRANSITIONS.get(current_status, [])
        # Active is always a valid next status (back on market)
        if 'Active' not in statuses:
            statuses = ['Active'] + statuses
        return statuses


# Export classes
__all__ = ['EmailProcessor', 'StatusFlowValidator']

