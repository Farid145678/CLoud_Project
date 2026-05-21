"""
Lambda function — triggered by S3 PUT on images/original/
Generates a 400x400 thumbnail and saves to images/thumb/
Same key structure, different prefix.
"""
import os
import io
import boto3
from PIL import Image

s3 = boto3.client("s3")

def lambda_handler(event, context):
    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key    = record["s3"]["object"]["key"]

        # Only process originals
        if not key.startswith("images/original/"):
            print(f"Skipping non-original key: {key}")
            continue

        print(f"Processing: s3://{bucket}/{key}")

        # Download original
        response = s3.get_object(Bucket=bucket, Key=key)
        img_data  = response["Body"].read()

        # Generate thumbnail
        img = Image.open(io.BytesIO(img_data))
        img.thumbnail((400, 400))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        buf.seek(0)

        # Save thumbnail
        thumb_key = key.replace("images/original/", "images/thumb/", 1)
        s3.put_object(
            Bucket=bucket,
            Key=thumb_key,
            Body=buf.read(),
            ContentType="image/jpeg",
        )
        print(f"Thumbnail saved: s3://{bucket}/{thumb_key}")

    return {"statusCode": 200}
