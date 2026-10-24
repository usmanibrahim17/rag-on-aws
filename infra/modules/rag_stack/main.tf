data "aws_caller_identity" "current" {}

locals {
  # Account ID suffix keeps the globally-unique S3 bucket name collision-free.
  docs_bucket_name   = "${var.name_prefix}-docs-${data.aws_caller_identity.current.account_id}"
  vector_bucket_name = "${var.name_prefix}-vectors-${data.aws_caller_identity.current.account_id}"
}

# ---------------------------------------------------------------------------
# Docs bucket: source Markdown/text files that the ingest Lambda reads.
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "docs" {
  bucket        = local.docs_bucket_name
  force_destroy = var.force_destroy
  tags          = var.tags
}

resource "aws_s3_bucket_public_access_block" "docs" {
  bucket                  = aws_s3_bucket.docs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "docs" {
  bucket = aws_s3_bucket.docs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Versioning lets us recover an accidentally overwritten or deleted doc.
resource "aws_s3_bucket_versioning" "docs" {
  bucket = aws_s3_bucket.docs.id

  versioning_configuration {
    status = "Enabled"
  }
}

# ---------------------------------------------------------------------------
# S3 Vectors: the vector store (bucket + one index).
# ---------------------------------------------------------------------------
resource "aws_s3vectors_vector_bucket" "this" {
  vector_bucket_name = local.vector_bucket_name
  force_destroy      = var.force_destroy
  tags               = var.tags
}

resource "aws_s3vectors_index" "chunks" {
  vector_bucket_name = aws_s3vectors_vector_bucket.this.vector_bucket_name
  index_name         = "${var.name_prefix}-chunks"

  data_type       = "float32"
  dimension       = var.embedding_dimension
  distance_metric = "cosine"

  metadata_configuration {
    # The chunk text is returned with query results but never filtered on,
    # so marking it non-filterable keeps it out of the filter index and
    # allows larger values.
    non_filterable_metadata_keys = ["chunk_text"]
  }
}

# ---------------------------------------------------------------------------
# Budget: email alerts at 40/80/100% actual spend and 100% forecasted.
# ---------------------------------------------------------------------------
resource "aws_budgets_budget" "monthly" {
  name         = "${var.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = [40, 80, 100]

    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = var.budget_alert_emails
    }
  }

  # Early warning: AWS projects month-end spend will exceed the budget.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = var.budget_alert_emails
  }
}