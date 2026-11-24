# Ingest pacing: S3 -> SQS -> Lambda.
#
# Without a queue, uploading N docs started N Lambdas at once and overwhelmed the AI provider's
# rate limit. The queue buffers the events and the event source mapping caps how many ingest
# Lambdas run in parallel. Messages that keep failing land in a dead-letter queue.

locals {
  # Visibility timeout must exceed the Lambda timeout, or a slow ingest is delivered twice
  # while still running. AWS recommends at least 6x the function timeout.
  ingest_visibility_timeout_seconds = local.ingest_timeout_seconds * 6
}

resource "aws_sqs_queue" "ingest_dlq" {
  name                      = "${var.name_prefix}-ingest-dlq"
  message_retention_seconds = 14 * 24 * 3600 # 14 days to investigate and redrive
  sqs_managed_sse_enabled   = true
  tags                      = var.tags
}

resource "aws_sqs_queue" "ingest" {
  name                       = "${var.name_prefix}-ingest"
  visibility_timeout_seconds = local.ingest_visibility_timeout_seconds
  message_retention_seconds  = 4 * 24 * 3600 # 4 days
  sqs_managed_sse_enabled    = true

  # After 5 failed receives a message moves to the DLQ instead of retrying forever.
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingest_dlq.arn
    maxReceiveCount     = 5
  })

  tags = var.tags
}

# Only S3, only for this bucket, only from this account, may send messages.
data "aws_iam_policy_document" "ingest_queue" {
  statement {
    sid       = "AllowS3SendMessage"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.ingest.arn]

    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_s3_bucket.docs.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_sqs_queue_policy" "ingest" {
  queue_url = aws_sqs_queue.ingest.id
  policy    = data.aws_iam_policy_document.ingest_queue.json
}

# One filter per suffix (S3 filters can't OR suffixes). ObjectRemoved:* also covers
# DeleteMarkerCreated, which is what a normal delete on a versioned bucket emits.
resource "aws_s3_bucket_notification" "docs" {
  bucket = aws_s3_bucket.docs.id

  queue {
    queue_arn     = aws_sqs_queue.ingest.arn
    events        = ["s3:ObjectCreated:*", "s3:ObjectRemoved:*"]
    filter_prefix = "docs/"
    filter_suffix = ".md"
  }

  queue {
    queue_arn     = aws_sqs_queue.ingest.arn
    events        = ["s3:ObjectCreated:*", "s3:ObjectRemoved:*"]
    filter_prefix = "docs/"
    filter_suffix = ".txt"
  }

  queue {
    queue_arn     = aws_sqs_queue.ingest.arn
    events        = ["s3:ObjectCreated:*", "s3:ObjectRemoved:*"]
    filter_prefix = "docs/"
    filter_suffix = ".pdf"
  }

  # S3 checks it may send to the queue when saving the notification, so the policy must exist first.
  depends_on = [aws_sqs_queue_policy.ingest]
}

# Lambda polls the queue. batch size 1 = one S3 event per invocation, so a failure only
# retries that document. maximum_concurrency is the pacing knob (AWS minimum is 2).
resource "aws_lambda_event_source_mapping" "ingest" {
  event_source_arn = aws_sqs_queue.ingest.arn
  function_name    = aws_lambda_function.ingest.arn
  batch_size       = 1

  # Lets the handler report which messages failed (batchItemFailures) instead of failing the batch.
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = var.ingest_max_concurrency
  }

  # The role must be allowed to read the queue before the mapping is created.
  depends_on = [aws_iam_role_policy.ingest]
}
