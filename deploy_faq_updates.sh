#!/bin/bash
set -e

echo "Deploying FAQ updates..."

# Step 1: Update FAQ entries
echo "Step 1: Updating FAQ entries..."
python update_faq.py

# Step 2: Re-ingest to update embeddings
echo "Step 2: Re-ingesting FAQ to update embeddings..."
python reingest_faq.py

echo "FAQ updates deployed successfully!"
echo "The chatbot will now use the updated FAQ answers immediately."
