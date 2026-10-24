# IAM roles for the ingest and query Lambdas are added in M2 and M3.
# Rule: each role gets only the actions it uses, scoped to the specific ARNs
# exported from main.tf (docs bucket, vector index, Bedrock model).
