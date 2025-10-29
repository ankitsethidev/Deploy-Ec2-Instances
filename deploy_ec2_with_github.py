#!/usr/bin/env python3
import os
import time
import json
import boto3
import requests
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# ---------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------
load_dotenv()  # Optional: load environment vars from .env

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
INSTANCE_TYPE = os.getenv("INSTANCE_TYPE", "t3.micro")
INSTANCE_COUNT = int(os.getenv("INSTANCE_COUNT", "50"))
KEY_PAIR_NAME = os.getenv("KEY_PAIR_NAME", "deployer-key")
SECURITY_GROUP_NAME = os.getenv("SECURITY_GROUP_NAME", "allow_ssh_http")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME")
GITHUB_REPO_NAME = os.getenv("GITHUB_REPO_NAME", "aws-ec2-50")
AMI_NAME_FILTER = os.getenv("AMI_NAME_FILTER", "amzn2-ami-kernel-5.10-hvm-*-x86_64-gp2")

# ---------------------------------------------------------------
# AWS SETUP
# ---------------------------------------------------------------
ec2 = boto3.client("ec2", region_name=AWS_REGION)
ec2_resource = boto3.resource("ec2", region_name=AWS_REGION)

# ---------------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------------
def create_github_repo():
    """Creates a GitHub repository via REST API"""
    if not GITHUB_TOKEN or not GITHUB_USERNAME:
        raise ValueError("Missing GITHUB_TOKEN or GITHUB_USERNAME environment variables.")

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    data = {
        "name": GITHUB_REPO_NAME,
        "description": "Automatically created by Python script to deploy 50 AWS EC2 instances",
        "private": False,
    }

    print(f"📦 Creating GitHub repository '{GITHUB_REPO_NAME}'...")

    resp = requests.post("https://api.github.com/user/repos", headers=headers, data=json.dumps(data))

    if resp.status_code in [201, 202]:
        print(f"✅ Repository created: https://github.com/{GITHUB_USERNAME}/{GITHUB_REPO_NAME}")
        return resp.json()["html_url"]
    elif resp.status_code == 422:
        print("⚠️ Repository already exists — skipping creation.")
        return f"https://github.com/{GITHUB_USERNAME}/{GITHUB_REPO_NAME}"
    else:
        raise Exception(f"GitHub API error: {resp.status_code} - {resp.text}")

def get_default_vpc_id():
    """Retrieve the default VPC ID"""
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    return vpcs["Vpcs"][0]["VpcId"]

def create_security_group(vpc_id):
    """Create a security group that allows SSH and HTTP"""
    try:
        response = ec2.create_security_group(
            GroupName=SECURITY_GROUP_NAME,
            Description="Allow SSH and HTTP inbound",
            VpcId=vpc_id
        )
        sg_id = response["GroupId"]
        print(f"✅ Security group created: {sg_id}")

        ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 22,
                    "ToPort": 22,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                },
                {
                    "IpProtocol": "tcp",
                    "FromPort": 80,
                    "ToPort": 80,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                },
            ],
        )
        return sg_id
    except ClientError as e:
        if "InvalidGroup.Duplicate" in str(e):
            print("⚠️ Security group already exists — reusing it.")
            sgs = ec2.describe_security_groups(GroupNames=[SECURITY_GROUP_NAME])
            return sgs["SecurityGroups"][0]["GroupId"]
        else:
            raise

def get_latest_ami():
    """Get the latest Amazon Linux 2 AMI"""
    images = ec2.describe_images(
        Owners=["amazon"],
        Filters=[{"Name": "name", "Values": [AMI_NAME_FILTER]}]
    )["Images"]
    sorted_images = sorted(images, key=lambda x: x["CreationDate"], reverse=True)
    return sorted_images[0]["ImageId"]

def create_key_pair():
    """Create or reuse an EC2 key pair"""
    try:
        key_pair = ec2.create_key_pair(KeyName=KEY_PAIR_NAME)
        private_key = key_pair["KeyMaterial"]
        key_path = f"{KEY_PAIR_NAME}.pem"
        with open(key_path, "w") as f:
            f.write(private_key)
        os.chmod(key_path, 0o400)
        print(f"✅ Created and saved new key pair at {key_path}")
    except ClientError as e:
        if "InvalidKeyPair.Duplicate" in str(e):
            print("⚠️ Key pair already exists — reusing it.")
        else:
            raise

def launch_instances(ami_id, sg_id):
    """Launch N EC2 instances"""
    print(f"🚀 Launching {INSTANCE_COUNT} EC2 instances of type {INSTANCE_TYPE}...")

    instances = ec2_resource.create_instances(
        ImageId=ami_id,
        MinCount=INSTANCE_COUNT,
        MaxCount=INSTANCE_COUNT,
        InstanceType=INSTANCE_TYPE,
        KeyName=KEY_PAIR_NAME,
        SecurityGroupIds=[sg_id],
        UserData="""#!/bin/bash
                    yum update -y
                    yum install -y httpd
                    systemctl enable httpd
                    systemctl start httpd
                    echo 'Hello from EC2 Python deployer!' > /var/www/html/index.html
                 """,
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [{"Key": "Name", "Value": "PythonDeployedEC2"}],
            }
        ],
    )

    print("⏳ Waiting for instances to be running...")
    for instance in instances:
        instance.wait_until_running()

    instances = [i.reload() or i for i in instances]
    public_ips = [i.public_ip_address for i in instances]
    print("✅ EC2 instances launched successfully:")
    for ip in public_ips:
        print(f"   → {ip}")
    return public_ips

# ---------------------------------------------------------------
# MAIN EXECUTION
# ---------------------------------------------------------------
def main():
    print("🌍 Starting deployment...")

    # Create GitHub repo
    repo_url = create_github_repo()

    # AWS resources
    create_key_pair()
    vpc_id = get_default_vpc_id()
    sg_id = create_security_group(vpc_id)
    ami_id = get_latest_ami()

    ips = launch_instances(ami_id, sg_id)

    print("\n🎯 Deployment Summary:")
    print(f"GitHub Repo: {repo_url}")
    print(f"Instance Count: {INSTANCE_COUNT}")
    print("Public IPs:")
    for ip in ips:
        print(f"  - {ip}")

    print("\n🧹 To clean up later, run:")
    print("  aws ec2 describe-instances --filters Name=tag:Name,Values=PythonDeployedEC2")
    print("  aws ec2 terminate-instances --instance-ids <ids>")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ Error: {e}")
