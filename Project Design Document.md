# **Design Document: Multi-Agent AI System for Maharashtra Legal Document Generation (PBL-2)**

## **1\. Executive Summary**

This project addresses the manual, error-prone nature of legal drafting in India, specifically targeting the jurisdiction of **Maharashtra (Pune/Mumbai)**. We propose a tiered architecture: starting with a high-velocity **Cache-Augmented Generation (CAG)** prototype for immediate workflow validation, followed by a comparative analysis of **Dense, Hybrid, and Agentic RAG** pipelines. The system leverages **DeepSeek OCR** for precise land record parsing and multi-agent orchestration for citation-grounded reasoning.

## **2\. Problem Statement**

* **Manual Dependency:** Reliance on outdated physical templates and personal memory.  
* **Jurisdictional Blindness:** Generic AI models fail to account for Maharashtra-specific statutes (e.g., MLRC 1966, MahaRERA).  
* **Hallucination Risk:** Critical legal citations and clauses are often fabricated by standard LLMs.  
* **Data Silos:** Lack of integration between Mahabhulekh (Land Records), IGR (Registration), and Judicial (High Court) data.

## **3\. Phase 1: Rapid 24-Hour Prototype (CAG Approach)**

**Objective:** Create a functional end-to-end "Mockup" to demonstrate the drafting workflow to research mentors and initiate paper drafting.

### **3.1 Data Ingestion: DeepSeek OCR Pipeline**

* **Input:** PDF/Scanned images of 7/12 Extracts, Property Cards, and standard local notices.  
* **Mechanism:** DeepSeek OCR (specifically optimized for layout preservation) extracts structured data from complex tables.  
* **Multilingual Normalization:** Automatic conversion of Marathi land record headers to canonical English IDs for model processing.

### **3.2 The CAG (Cache-Augmented Generation) Architecture**

Unlike RAG, which retrieves data per query, CAG pre-loads a "Jurisdictional Legal Cache" into the model's context window.

* **The Cache:** A curated set of "Must-Have" legal documents for the specific session (e.g., The Transfer of Property Act \+ Recent 5 Bombay HC Judgments on Land Encroachment).  
* **Workflow:**  
  1. User uploads a fact pattern or a scanned record.  
  2. System triggers **DeepSeek OCR** to extract facts.  
  3. The extracted facts \+ **Pre-injected Legal Cache** are sent to the LLM (Llama 3 / Qwen).  
  4. **Drafting:** LLM generates a document grounded strictly in the "Cache" provided.  
* **Advantage:** Zero-latency retrieval, high consistency, and immediate "proof of concept."

## **4\. Phase 2: Systematic RAG Architecture & Benchmarking**

After the CAG prototype, the system transitions to a retrieval-based framework to handle massive datasets from IGR and High Court portals.

### **4.1 Knowledge Representation & Multi-Index Store**

* **Metadata Tagging:** Every chunk is indexed with \[Court\_Type, Year, Statute\_Reference, Language, District\].  
* **Chunking Strategy:** Semantic legal chunking (splitting by Clauses/Sections) instead of fixed character counts.

### **4.2 Parallel Retrieval Pipelines**

We implement and compare four distinct pipelines:

1. **Dense RAG (Baseline):**  
   * *Retrieval:* Vector similarity search using bge-m3 or LaBSE embeddings.  
   * *Metric:* Establishes a baseline for semantic relevance vs. hallucination.  
2. **Hybrid RAG (Precision):**  
   * *Retrieval:* Weighted combination of Dense Vector Search \+ BM25 Sparse Search \+ Metadata Filters (e.g., "Only 2023 Bombay HC").  
   * *Metric:* Citation accuracy and jurisdictional compliance.  
3. **Agentic RAG (Reasoning Depth):**  
   * *Framework:* **LangGraph** or **Haystack Agents**.  
   * *Agents:*  
     * **Issue Agent:** Parses facts to find legal "Points of Contention."  
     * **Statute Agent:** Specifically searches for relevant Maharashtra Acts.  
     * **Case Law Agent:** Finds recent District/High Court precedents.  
     * **Conflict Agent:** Resolves contradictions between retrieved statutes.  
   * *Metric:* Reasoning depth and faithfulness to authoritative sources.

## **5\. Data Privacy & Anonymization**

To ensure ethical compliance for Maharashtra-specific data:

* **Redaction Layer:** Integrated during ingestion.  
* **Hybrid NER:** Use spaCy/BERT for general entities \+ custom RegEx for **Aadhaar, PAN, and Survey/Gat numbers**.  
* **Differential Privacy:** Replace real names with placeholders (e.g., \<PETITIONER\_1\>) to maintain document logic while protecting PII.

## **6\. Implementation Stack (Open-Source)**

| Layer | Technologies |
| :---- | :---- |
| **OCR & Parsing** | DeepSeek OCR, PyMuPDF, Tesseract |
| **Embeddings** | bge-m3, LaBSE (Multilingual focus) |
| **Vector Database** | Qdrant / FAISS |
| **LLMs** | LLaMA 3 (8B/70B), Qwen 2, Mixtral |
| **Orchestration** | LangGraph, Haystack |
| **Evaluation** | RAGAS (Retrieval, Faithfulness, Answer Relevance) |

## **7\. Comparative Benchmarking Metrics**

For the research paper, each pipeline (CAG vs. Dense vs. Hybrid vs. Agentic) will be scored on:

1. **Hallucination Rate:** Frequency of non-existent citations.  
2. **Jurisdictional Accuracy:** Adherence to Mumbai/Pune-specific bylaws.  
3. **Source Coverage:** Percentage of document content traced back to IGR/High Court data.  
4. **Latency:** Time taken to generate a full 5-page draft.

## **8\. Deployment Strategy**

* **Cloud Platform:** Containerized deployment using Docker on AWS/GCP.  
* **Frontend:** Streamlit or React dashboard for legal professionals to interact with the generated drafts and view "Reasoning Traces."  
* **Output:** Word/PDF documents with hyperlinked citations to original sources.