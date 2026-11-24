# Permissions of the GitHub deploy role: "manage the rag-demo stack, and nothing else".
#
# Four customer-managed policies (each under the 6,144-character limit). Wherever AWS allows
# it, write access is scoped to resources named <prefix>-*; the exceptions are listed in the
# comments (and in the README of this folder) because their ARNs are not name-based.

locals {
  p      = var.name_prefix
  region = var.region
  acct   = local.account_id

  docs_bucket    = "arn:aws:s3:::${local.p}-docs-${local.acct}"
  web_bucket     = "arn:aws:s3:::${local.p}-web-${local.acct}"
  vector_bucket  = "arn:aws:s3vectors:${local.region}:${local.acct}:bucket/${local.p}-vectors-${local.acct}"
  lambda_arn     = "arn:aws:lambda:${local.region}:${local.acct}:function:${local.p}-*"
  sqs_arn        = "arn:aws:sqs:${local.region}:${local.acct}:${local.p}-*"
  role_arn       = "arn:aws:iam::${local.acct}:role/${local.p}-*"
  deploy_role    = "arn:aws:iam::${local.acct}:role/${local.deploy_role_name}"
  budget_arn     = "arn:aws:budgets::${local.acct}:budget/${local.p}-*"
  boundary_arn   = "arn:aws:iam::${local.acct}:policy/${local.p}-stack-boundary"
  lambda_logs    = "arn:aws:logs:${local.region}:${local.acct}:log-group:/aws/lambda/${local.p}-*"
  apigw_logs     = "arn:aws:logs:${local.region}:${local.acct}:log-group:/aws/apigateway/${local.p}-*"
  apigw_http_api = "arn:aws:apigateway:${local.region}::/apis"
}

# --- 1. Terraform state + data stores (S3, S3 Vectors, SQS, Budgets) ----------------------
data "aws_iam_policy_document" "deploy_storage" {
  statement {
    sid       = "StateBucketList"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:GetBucketVersioning"]
    resources = [aws_s3_bucket.state.arn]
  }

  # The native lockfile (use_lockfile) is an object next to the state, so Put/Delete are needed.
  statement {
    sid       = "StateObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/*"]
  }

  # Full control of the two stack buckets only (creation, config, objects, deletion).
  statement {
    sid     = "StackBuckets"
    actions = ["s3:*"]
    resources = [
      local.docs_bucket, "${local.docs_bucket}/*",
      local.web_bucket, "${local.web_bucket}/*",
    ]
  }

  statement {
    sid       = "StackVectorBucket"
    actions   = ["s3vectors:*"]
    resources = [local.vector_bucket, "${local.vector_bucket}/*"]
  }

  # Listing has no resource-level scope. Read-only.
  statement {
    sid       = "ListVectorBuckets"
    actions   = ["s3vectors:ListVectorBuckets"]
    resources = ["*"]
  }

  statement {
    sid       = "StackQueues"
    actions   = ["sqs:*"]
    resources = [local.sqs_arn]
  }

  statement {
    sid       = "ListQueues"
    actions   = ["sqs:ListQueues"]
    resources = ["*"]
  }

  statement {
    sid       = "StackBudget"
    actions   = ["budgets:*"]
    resources = [local.budget_arn]
  }
}

# --- 2. Compute, API and IAM roles of the stack -------------------------------------------
data "aws_iam_policy_document" "deploy_compute" {
  statement {
    sid       = "StackFunctions"
    actions   = ["lambda:*"]
    resources = [local.lambda_arn]
  }

  # Event source mappings are identified by a random UUID, so they cannot be scoped by name.
  statement {
    sid = "EventSourceMappings"
    actions = [
      "lambda:CreateEventSourceMapping",
      "lambda:GetEventSourceMapping",
      "lambda:UpdateEventSourceMapping",
      "lambda:DeleteEventSourceMapping",
      "lambda:ListEventSourceMappings",
      "lambda:TagResource",
      "lambda:UntagResource",
      "lambda:ListTags",
    ]
    resources = ["*"]
  }

  # HTTP APIs are addressed by random IDs, so they cannot be scoped by name: this allows
  # managing any HTTP API in the account/region.
  statement {
    sid       = "HttpApis"
    actions   = ["apigateway:GET", "apigateway:POST", "apigateway:PUT", "apigateway:PATCH", "apigateway:DELETE"]
    resources = [local.apigw_http_api, "${local.apigw_http_api}/*", "arn:aws:apigateway:${local.region}::/tags/*"]
  }

  statement {
    sid       = "StackLogGroups"
    actions   = ["logs:*"]
    resources = [local.lambda_logs, "${local.lambda_logs}:*", local.apigw_logs, "${local.apigw_logs}:*"]
  }

  # API Gateway access logging needs a CloudWatch Logs resource policy plus log-delivery
  # permissions; these actions have no resource-level scope (account-wide).
  statement {
    sid = "LogDeliveryForApiGateway"
    actions = [
      "logs:DescribeLogGroups",
      "logs:DescribeResourcePolicies",
      "logs:PutResourcePolicy",
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
    ]
    resources = ["*"]
  }
}

# --- 3. Demo site delivery (CloudFront) ---------------------------------------------------
data "aws_iam_policy_document" "deploy_edge" {
  # Distributions, access controls and policies get random IDs at creation, so these cannot
  # be scoped by name; the role can manage any CloudFront resource in the account.
  statement {
    sid = "CloudFront"
    actions = [
      "cloudfront:CreateDistribution",
      "cloudfront:GetDistribution",
      "cloudfront:GetDistributionConfig",
      "cloudfront:UpdateDistribution",
      "cloudfront:DeleteDistribution",
      "cloudfront:ListDistributions",
      "cloudfront:CreateOriginAccessControl",
      "cloudfront:GetOriginAccessControl",
      "cloudfront:UpdateOriginAccessControl",
      "cloudfront:DeleteOriginAccessControl",
      "cloudfront:CreateCachePolicy",
      "cloudfront:GetCachePolicy",
      "cloudfront:UpdateCachePolicy",
      "cloudfront:DeleteCachePolicy",
      "cloudfront:CreateResponseHeadersPolicy",
      "cloudfront:GetResponseHeadersPolicy",
      "cloudfront:UpdateResponseHeadersPolicy",
      "cloudfront:DeleteResponseHeadersPolicy",
      "cloudfront:TagResource",
      "cloudfront:UntagResource",
      "cloudfront:ListTagsForResource",
      "cloudfront:CreateInvalidation",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "deploy_storage" {
  name   = "${local.deploy_role_name}-storage"
  policy = data.aws_iam_policy_document.deploy_storage.json
  tags   = var.tags
}

resource "aws_iam_policy" "deploy_compute" {
  name   = "${local.deploy_role_name}-compute"
  policy = data.aws_iam_policy_document.deploy_compute.json
  tags   = var.tags
}

resource "aws_iam_policy" "deploy_iam" {
  name   = "${local.deploy_role_name}-iam"
  policy = data.aws_iam_policy_document.deploy_iam.json
  tags   = var.tags
}

resource "aws_iam_policy" "deploy_edge" {
  name   = "${local.deploy_role_name}-edge"
  policy = data.aws_iam_policy_document.deploy_edge.json
  tags   = var.tags
}

resource "aws_iam_role_policy_attachment" "deploy" {
  for_each = {
    storage = aws_iam_policy.deploy_storage.arn
    compute = aws_iam_policy.deploy_compute.arn
    iam     = aws_iam_policy.deploy_iam.arn
    edge    = aws_iam_policy.deploy_edge.arn
  }

  role       = aws_iam_role.deploy.name
  policy_arn = each.value
}
