#!/usr/bin/env python3
"""
Migration script to convert existing FAQ data to JSONL format.

This script converts Excel or other FAQ sources to the new JSONL format.
"""

import json
import os
import sys
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent))


def migrate_from_jsonl_old(input_path: str, output_path: str) -> int:
    """
    Migrate from old JSONL format to new standardized format.

    Args:
        input_path: Path to old JSONL file
        output_path: Path to new JSONL file

    Returns:
        Number of entries migrated
    """
    if not os.path.exists(input_path):
        print(f"❌ Input file not found: {input_path}")
        return 0

    entries = []
    line_count = 0

    print(f"📖 Reading from: {input_path}")

    with open(input_path, 'r', encoding='utf-8') as f:
        for line in f:
            line_count += 1
            line = line.strip()

            if not line:
                continue

            try:
                obj = json.loads(line)

                # Extract question and answer from various formats
                question = (
                    obj.get("question") or
                    obj.get("vraag") or
                    obj.get("Vraag") or
                    obj.get("Question") or
                    ""
                ).strip()

                answer = (
                    obj.get("answer") or
                    obj.get("antwoord") or
                    obj.get("Antwoord") or
                    obj.get("Answer") or
                    ""
                ).strip()

                if not question or not answer:
                    print(f"⚠️  Line {line_count}: Missing question or answer, skipping")
                    continue

                # Build new standardized entry
                entry = {
                    "question": question,
                    "answer": answer
                }

                # Add category if present
                category = (
                    obj.get("category") or
                    obj.get("categorie") or
                    obj.get("Categorie") or
                    ""
                ).strip()

                if category:
                    entry["category"] = category

                # Add tags if present
                tags = obj.get("tags", [])
                if isinstance(tags, list) and tags:
                    entry["tags"] = [str(t).strip() for t in tags if str(t).strip()]

                # Add metadata
                metadata = {}

                # Preserve any additional fields
                for key, value in obj.items():
                    if key not in ["question", "vraag", "Vraag", "Question",
                                   "answer", "antwoord", "Antwoord", "Answer",
                                   "category", "categorie", "Categorie",
                                   "tags"]:
                        metadata[key] = value

                if metadata:
                    entry["metadata"] = metadata

                entries.append(entry)

            except json.JSONDecodeError as e:
                print(f"❌ Line {line_count}: JSON error: {e}")
                continue
            except Exception as e:
                print(f"❌ Line {line_count}: Error: {e}")
                continue

    if not entries:
        print("❌ No entries to migrate")
        return 0

    # Write to new format
    print(f"💾 Writing to: {output_path}")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    print(f"✅ Migrated {len(entries)} entries")
    return len(entries)


def create_sample_faq(output_path: str) -> int:
    """
    Create a sample FAQ JSONL file for testing.

    Args:
        output_path: Path to output file

    Returns:
        Number of entries created
    """
    sample_entries = [
        {
            "question": "How do I reset my Wifipool device?",
            "answer": "To reset your Wifipool: 1) Press and hold the reset button for 10 seconds. 2) Wait for the LED to blink. 3) The device will restart automatically.",
            "category": "Device Management",
            "tags": ["reset", "wifipool", "troubleshooting"]
        },
        {
            "question": "How to calibrate pH sensor?",
            "answer": "To calibrate the pH sensor: 1) Prepare calibration solutions (pH 4.0 and pH 7.0). 2) Rinse the sensor with distilled water. 3) Place in pH 7.0 solution and press 'Calibrate'. 4) Repeat with pH 4.0 solution.",
            "category": "Sensor Calibration",
            "tags": ["ph", "sensor", "calibration"]
        },
        {
            "question": "WiFi connection problems",
            "answer": "If you have WiFi connection issues: 1) Check that your router is working. 2) Verify the password is correct. 3) Make sure the device is within WiFi range. 4) Try restarting the device.",
            "category": "Connectivity",
            "tags": ["wifi", "connection", "troubleshooting"]
        },
        {
            "question": "How to connect Wifipool to WiFi network?",
            "answer": "To connect your Wifipool to WiFi: 1) Power on the device. 2) Connect to the Wifipool network from your phone. 3) Open the Wifipool app. 4) Select your home WiFi network and enter the password. 5) Wait for confirmation.",
            "category": "Setup",
            "tags": ["wifi", "setup", "connection"]
        },
        {
            "question": "Chlorine level too high, what to do?",
            "answer": "If chlorine is too high: 1) Stop adding chlorine products. 2) Let the sun naturally reduce chlorine levels. 3) Partially drain and refill with fresh water. 4) Do not swim until levels normalize (1-3 ppm).",
            "category": "Water Chemistry",
            "tags": ["chlorine", "chemistry", "safety"]
        },
    ]

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        for entry in sample_entries:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    print(f"✅ Created {len(sample_entries)} sample FAQ entries")
    return len(sample_entries)


def main():
    """Main migration script."""
    print("\n" + "="*60)
    print("FAQ TO JSONL MIGRATION SCRIPT")
    print("="*60)

    # Check for existing FAQ files
    old_jsonl = "app/data/all/faq/FAQAI.jsonl"
    new_jsonl = "app/data/faq.jsonl"

    if os.path.exists(old_jsonl):
        print(f"\n📁 Found existing JSONL: {old_jsonl}")
        print(f"🔄 Migrating to new format: {new_jsonl}")

        count = migrate_from_jsonl_old(old_jsonl, new_jsonl)

        if count > 0:
            print(f"\n✅ Migration successful!")
            print(f"   Old file: {old_jsonl}")
            print(f"   New file: {new_jsonl}")
            print(f"   Entries: {count}")
        else:
            print("\n❌ Migration failed")
            return 1

    elif os.path.exists(new_jsonl):
        print(f"\n✅ FAQ already in new format: {new_jsonl}")

        # Count entries
        count = 0
        with open(new_jsonl, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    count += 1

        print(f"   Entries: {count}")

    else:
        print(f"\n⚠️  No existing FAQ found")
        print(f"📝 Creating sample FAQ: {new_jsonl}")

        count = create_sample_faq(new_jsonl)

        print(f"\n✅ Sample FAQ created with {count} entries")
        print(f"   You can now add your own entries to: {new_jsonl}")

    print("\n" + "="*60)
    print("NEXT STEPS:")
    print("="*60)
    print("1. Review the FAQ file: " + new_jsonl)
    print("2. Add/edit entries as needed")
    print("3. Run: python3 -m app.faq_jsonl to build embeddings")
    print("4. Restart the chatbot")
    print("="*60)

    return 0


if __name__ == "__main__":
    exit(main())
