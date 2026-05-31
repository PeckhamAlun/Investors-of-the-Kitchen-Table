import chromadb

client = chromadb.PersistentClient(path="./chroma_db")
collections = client.list_collections()

search_term = "120%"

for col_info in collections:
    col = client.get_collection(col_info.name)
    results = col.get(include=["documents", "metadatas"])
    
    matches = [
        (doc, meta) for doc, meta in 
        zip(results["documents"], results["metadatas"])
        if search_term in doc
    ]
    
    if matches:
        print(f"\nCOLLECTION: {col_info.name} — {len(matches)} matches")
        for doc, meta in matches[:3]:
            print(f"Source: {meta.get('source')}")
            print(f"{doc[:400]}")
            print("---")

print("Search complete.")