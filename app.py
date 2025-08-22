import os
import re
import uuid
import fitz  # PyMuPDF
import pandas as pd
import gradio as gr
from typing import List, Tuple, Dict, Any
from pydantic import BaseModel
from datetime import datetime
import tempfile

# -----------------------------
# Enhanced Regex patterns for financial documents
# -----------------------------
NAME_PAT = re.compile(r"(?:Name|Full Name)[:\s]+([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)", re.IGNORECASE)
DOB_PAT = re.compile(r"(?:Date of Birth|DOB|Born)[:\s]*(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})", re.IGNORECASE)
IDNUM_PAT = re.compile(r"(?:Passport|ID|License)\s*(?:No|Number)[:\s]*([A-Z0-9]{6,})", re.IGNORECASE)
EXP_PAT = re.compile(r"(?:Exp|Expiry|Expires)[:\s]*(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})", re.IGNORECASE)
ADDRESS_PAT = re.compile(r"(?:Address|Residence)[:\s]*([A-Za-z0-9\s,]+(?:\d{5})?)", re.IGNORECASE)
ACCOUNT_PAT = re.compile(r"(?:Account|Acc)\s*(?:No|Number)[:\s]*(\d{8,})", re.IGNORECASE)

# -----------------------------
# Enhanced keyword definitions for financial documents
# -----------------------------
DOC_KEYWORDS = {
    "passport": ["passport", "passport no", "republic of", "immigration"],
    "driver_license": ["driver license", "driving license", "dmv", "motor vehicle"],
    "utility_bill": ["utility bill", "electricity", "water bill", "gas bill", "internet bill", "phone bill"],
    "bank_statement": ["bank statement", "account statement", "balance", "transaction", "deposit"],
    "id_card": ["identity card", "national id", "citizen card", "identification"],
    "tax_document": ["tax return", "tax certificate", "irs", "revenue service", "w-2", "1099"],
    "proof_of_income": ["salary", "payslip", "employment letter", "income statement"],
    "proof_of_address": ["utility bill", "lease agreement", "rental agreement", "mortgage statement"]
}

# Enhanced requirement sets for different client types
REQUIRED_SETS = {
    "individual_basic": ["passport", "utility_bill"],
    "individual_full": ["passport", "driver_license", "bank_statement", "tax_document"],
    "business": ["passport", "utility_bill", "bank_statement", "tax_document", "proof_of_income"],
    "high_net_worth": ["passport", "utility_bill", "bank_statement", "tax_document", "proof_of_income", "proof_of_address"]
}

# -----------------------------
# Enhanced Pydantic models
# -----------------------------
class DocResult(BaseModel):
    client_id: str
    filename: str
    doc_type: str
    confidence_score: float
    status: str
    issues: List[str]
    extracted_data: Dict[str, Any]
    file_size_kb: int
    pages: int

class ClientChecklist(BaseModel):
    client_id: str
    required_set: str
    doc_type: str
    present: bool
    status: str
    compliance_score: float
    notes: str

class ValidationSummary(BaseModel):
    total_documents: int
    processed_successfully: int
    compliance_rate: float
    avg_confidence: float
    issues_found: int
    timestamp: str

# -----------------------------
# Enhanced helper functions
# -----------------------------
def extract_pdf_text(file_path: str) -> Tuple[str, int, int]:
    """Extract text from PDF and return text, page count, and file size"""
    try:
        doc = fitz.open(file_path)
        text = ""
        for page in doc:
            text += page.get_text()
        page_count = len(doc)
        doc.close()
        
        # Get file size in KB
        file_size_kb = os.path.getsize(file_path) // 1024
        
        return text, page_count, file_size_kb
    except Exception as e:
        return f"Error extracting text: {str(e)}", 0, 0

def classify_doc_type(text: str) -> Tuple[str, float]:
    """Classify document type with confidence score"""
    text_lower = text.lower()
    best_match = "unknown"
    best_score = 0.0
    
    for doc_type, keywords in DOC_KEYWORDS.items():
        score = 0
        for kw in keywords:
            if kw.lower() in text_lower:
                score += 1
        
        # Calculate confidence as percentage of keywords found
        confidence = (score / len(keywords)) * 100
        if confidence > best_score:
            best_score = confidence
            best_match = doc_type
    
    return best_match, min(best_score, 100.0)

def extract_structured_data(text: str, doc_type: str) -> Dict[str, Any]:
    """Extract structured data based on document type"""
    data = {}
    
    # Common fields for all documents
    name_match = NAME_PAT.search(text)
    if name_match:
        data["name"] = name_match.group(1).strip()
    
    # Document-specific extraction
    if doc_type in ["passport", "driver_license", "id_card"]:
        dob_match = DOB_PAT.search(text)
        if dob_match:
            data["date_of_birth"] = dob_match.group(1)
        
        id_match = IDNUM_PAT.search(text)
        if id_match:
            data["document_number"] = id_match.group(1)
        
        exp_match = EXP_PAT.search(text)
        if exp_match:
            data["expiry_date"] = exp_match.group(1)
    
    elif doc_type == "bank_statement":
        account_match = ACCOUNT_PAT.search(text)
        if account_match:
            data["account_number"] = account_match.group(1)
    
    # Address for utility bills and proof of address
    if doc_type in ["utility_bill", "proof_of_address"]:
        address_match = ADDRESS_PAT.search(text)
        if address_match:
            data["address"] = address_match.group(1).strip()
    
    return data

def validate_document_quality(text: str, doc_type: str, strict: bool = True) -> Tuple[str, List[str], float]:
    """Enhanced document validation with quality scoring"""
    issues = []
    quality_score = 100.0
    
    # Basic text quality checks
    if len(text.strip()) < 50:
        issues.append("Document appears to be empty or unreadable")
        quality_score -= 50
    
    # Document-specific validation
    if doc_type in ["passport", "driver_license", "id_card"]:
        if not NAME_PAT.search(text):
            issues.append("Name not found or not properly formatted")
            quality_score -= 20
        
        if not DOB_PAT.search(text):
            issues.append("Date of birth missing")
            quality_score -= 15
        
        if not IDNUM_PAT.search(text):
            issues.append("Document number missing")
            quality_score -= 20
        
        if strict and not EXP_PAT.search(text):
            issues.append("Expiry date missing")
            quality_score -= 10
    
    elif doc_type == "bank_statement":
        if not ACCOUNT_PAT.search(text):
            issues.append("Account number not found")
            quality_score -= 25
        
        if "balance" not in text.lower():
            issues.append("Balance information missing")
            quality_score -= 15
    
    elif doc_type == "utility_bill":
        if not ADDRESS_PAT.search(text):
            issues.append("Address not found")
            quality_score -= 30
        
        # Check for recent date (simplified)
        if not re.search(r"202[3-5]", text):
            issues.append("Document may be outdated")
            quality_score -= 10
    
    # Determine overall status
    if quality_score >= 80:
        status = "pass"
    elif quality_score >= 60:
        status = "warning"
    else:
        status = "fail"
    
    return status, issues, max(quality_score, 0.0)

def guess_client_id(filepath: str) -> str:
    """Extract client ID from filename or generate one"""
    base = os.path.basename(filepath)
    if "_" in base:
        potential_id = base.split("_")[0]
        if len(potential_id) >= 3:
            return potential_id
    return f"CLIENT_{uuid.uuid4().hex[:6].upper()}"

def validate_documents(files: List[str], required_set: str, strict_mode: bool) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """Main validation function with enhanced analytics"""
    doc_results: List[DocResult] = []
    checklist: List[ClientChecklist] = []
    
    total_docs = len(files)
    processed_successfully = 0
    total_confidence = 0
    total_issues = 0
    
    for file_path in files:
        try:
            client_id = guess_client_id(file_path)
            text, page_count, file_size_kb = extract_pdf_text(file_path)
            
            if "Error extracting text" in text:
                # Handle extraction error
                doc_results.append(DocResult(
                    client_id=client_id,
                    filename=os.path.basename(file_path),
                    doc_type="error",
                    confidence_score=0.0,
                    status="fail",
                    issues=[text],
                    extracted_data={},
                    file_size_kb=file_size_kb,
                    pages=page_count
                ))
                total_issues += 1
                continue
            
            doc_type, confidence = classify_doc_type(text)
            status, issues, quality_score = validate_document_quality(text, doc_type, strict_mode)
            extracted_data = extract_structured_data(text, doc_type)
            
            doc_results.append(DocResult(
                client_id=client_id,
                filename=os.path.basename(file_path),
                doc_type=doc_type,
                confidence_score=confidence,
                status=status,
                issues=issues,
                extracted_data=extracted_data,
                file_size_kb=file_size_kb,
                pages=page_count
            ))
            
            processed_successfully += 1
            total_confidence += confidence
            total_issues += len(issues)
            
        except Exception as e:
            doc_results.append(DocResult(
                client_id="ERROR",
                filename=os.path.basename(file_path),
                doc_type="error",
                confidence_score=0.0,
                status="fail",
                issues=[f"Processing error: {str(e)}"],
                extracted_data={},
                file_size_kb=0,
                pages=0
            ))
            total_issues += 1
    
    # Build compliance checklist
    req_docs = REQUIRED_SETS.get(required_set, [])
    client_groups = {}
    for doc in doc_results:
        if doc.client_id not in client_groups:
            client_groups[doc.client_id] = []
        client_groups[doc.client_id].append(doc)
    
    for client_id, client_docs in client_groups.items():
        present_types = {d.doc_type for d in client_docs if d.status != "fail"}
        
        for doc_type in req_docs:
            present = doc_type in present_types
            
            if present:
                # Calculate compliance score based on document quality
                matching_docs = [d for d in client_docs if d.doc_type == doc_type]
                avg_confidence = sum(d.confidence_score for d in matching_docs) / len(matching_docs)
                compliance_score = avg_confidence
                status = "compliant" if avg_confidence >= 70 else "needs_review"
                notes = f"Document found with {avg_confidence:.1f}% confidence"
            else:
                compliance_score = 0.0
                status = "missing"
                notes = "Required document not provided"
            
            checklist.append(ClientChecklist(
                client_id=client_id,
                required_set=required_set,
                doc_type=doc_type,
                present=present,
                status=status,
                compliance_score=compliance_score,
                notes=notes
            ))
    
    # Create summary statistics
    avg_confidence = (total_confidence / processed_successfully) if processed_successfully > 0 else 0
    compliance_rate = (len([c for c in checklist if c.status == "compliant"]) / len(checklist) * 100) if checklist else 0
    
    summary = {
        "total_documents": total_docs,
        "processed_successfully": processed_successfully,
        "compliance_rate": compliance_rate,
        "avg_confidence": avg_confidence,
        "issues_found": total_issues,
        "timestamp": datetime.now().isoformat()
    }
    
    # Convert to DataFrames
    doc_df = pd.DataFrame([d.dict() for d in doc_results])
    chk_df = pd.DataFrame([c.dict() for c in checklist])
    
    return doc_df, chk_df, summary

# -----------------------------
# Enhanced Gradio App
# -----------------------------
def run_validation(files, required_set, strict_mode):
    """Process uploaded files and return results"""
    if not files:
        return None, None, "No files uploaded", None, None
    
    # Save uploaded files temporarily
    temp_files = []
    for file in files:
        temp_files.append(file.name)
    
    try:
        doc_df, chk_df, summary = validate_documents(temp_files, required_set, strict_mode)
        
        # Create summary text
        summary_text = f"""
        📊 **Validation Summary**
        - Total Documents: {summary['total_documents']}
        - Successfully Processed: {summary['processed_successfully']}
        - Compliance Rate: {summary['compliance_rate']:.1f}%
        - Average Confidence: {summary['avg_confidence']:.1f}%
        - Issues Found: {summary['issues_found']}
        - Processed at: {summary['timestamp']}
        """
        
        # Save results to CSV
        doc_csv_path = f"doc_results_{uuid.uuid4().hex[:8]}.csv"
        chk_csv_path = f"checklist_{uuid.uuid4().hex[:8]}.csv"
        
        doc_df.to_csv(doc_csv_path, index=False)
        chk_df.to_csv(chk_csv_path, index=False)
        
        return doc_df, chk_df, summary_text, doc_csv_path, chk_csv_path
        
    except Exception as e:
        error_msg = f"❌ Error processing documents: {str(e)}"
        return None, None, error_msg, None, None

# Create Gradio interface
with gr.Blocks(title="KYC Document Validator", theme=gr.themes.Soft()) as demo:
    gr.Markdown("""
    # 🏛️ Client Onboarding Document Validator
    ### AI-Powered KYC Document Analysis for Financial Institutions
    
    Upload client documents to automatically validate compliance with KYC requirements.
    """)
    
    with gr.Row():
        with gr.Column(scale=2):
            file_input = gr.File(
                label="📄 Upload PDF Documents",
                file_types=[".pdf"],
                file_count="multiple"
            )
            
            with gr.Row():
                required_set = gr.Dropdown(
                    choices=list(REQUIRED_SETS.keys()),
                    label="👥 Client Type",
                    value="individual_basic",
                    info="Select the appropriate document requirements"
                )
                strict_mode = gr.Checkbox(
                    label="🔍 Strict Validation",
                    value=True,
                    info="Enable enhanced validation (recommended)"
                )
            
            run_btn = gr.Button("🚀 Validate Documents", variant="primary", size="lg")
        
        with gr.Column(scale=1):
            gr.Markdown("""
            ### 📋 Required Documents
            
            **Individual Basic:**
            - Passport
            - Utility Bill
            
            **Individual Full:**
            - Passport
            - Driver's License
            - Bank Statement
            - Tax Document
            
            **Business:**
            - All Individual docs
            - Proof of Income
            
            **High Net Worth:**
            - All Business docs
            - Proof of Address
            """)
    
    with gr.Row():
        summary_output = gr.Markdown(label="📊 Summary")
    
    with gr.Row():
        with gr.Column():
            doc_results = gr.Dataframe(
                label="📋 Document Analysis Results",
                wrap=True
            )
        
        with gr.Column():
            checklist_results = gr.Dataframe(
                label="✅ Compliance Checklist",
                wrap=True
            )
    
    with gr.Row():
        doc_csv_download = gr.File(label="📥 Download Document Results")
        checklist_csv_download = gr.File(label="📥 Download Compliance Report")
    
    # Event handler
    run_btn.click(
        fn=run_validation,
        inputs=[file_input, required_set, strict_mode],
        outputs=[doc_results, checklist_results, summary_output, doc_csv_download, checklist_csv_download]
    )

# Launch configuration
if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=True,  # Creates public ngrok tunnel
        show_error=True,
        show_api=False
    )
