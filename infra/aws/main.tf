# AWS base of one environment: the landing bucket, the role Snowflake assumes to read it, and the
# bucket notification that feeds Snowpipe. These are per-environment resources, not per-partner:
# onboarding a partner only adds a prefix inside the bucket and never changes anything here.

locals {
  platform = yamldecode(file("${path.module}/../../platform/${var.environment}.yml"))
  aws      = local.platform.aws
}

resource "aws_s3_bucket" "landing" {
  bucket = local.aws.bucket
}

resource "aws_s3_bucket_public_access_block" "landing" {
  bucket = aws_s3_bucket.landing.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# A partner can deliver a file again under the same name, and archiving removes files from
# landing. With versioning neither loses the earlier content: it stays as a noncurrent version
# for 90 days, long enough to investigate a delivery, and is then expired.
resource "aws_s3_bucket_versioning" "landing" {
  bucket = aws_s3_bucket.landing.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "landing" {
  # Noncurrent expiration only applies to a versioned bucket.
  depends_on = [aws_s3_bucket_versioning.landing]
  bucket     = aws_s3_bucket.landing.id

  rule {
    id     = "expire-replaced-and-removed-files"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    # Removes the marker a deletion leaves behind once no version is left under it.
    expiration {
      expired_object_delete_marker = true
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# Only the Snowflake principal of the storage integration can assume the role, and only with the
# external ID of that integration. Snowflake assumes it when a pipe is created, so this trust
# must be in place before the first partner is deployed.
data "aws_iam_policy_document" "snowflake_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [var.snowflake_iam_user_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [var.snowflake_external_id]
    }
  }
}

resource "aws_iam_role" "snowflake" {
  name               = local.aws.snowflake_role_name
  description        = "Assumed by the Snowflake storage integration ${local.platform.snowflake.storage_integration}"
  assume_role_policy = data.aws_iam_policy_document.snowflake_trust.json
}

data "aws_iam_policy_document" "snowflake_access" {
  statement {
    sid       = "ListBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.landing.arn]
  }

  statement {
    sid       = "ReadPartnerFiles"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${aws_s3_bucket.landing.arn}/*"]
  }

  # Processed files are moved from <partner>/landing/ to <partner>/historical/ from Snowflake,
  # so the pipeline needs no AWS credentials of its own.
  statement {
    sid       = "ArchiveWriteHistorical"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.landing.arn}/*/historical/*"]
  }

  statement {
    sid       = "ArchiveRemoveFromLanding"
    actions   = ["s3:DeleteObject", "s3:DeleteObjectVersion"]
    resources = ["${aws_s3_bucket.landing.arn}/*/landing/*"]
  }
}

resource "aws_iam_role_policy" "snowflake_access" {
  name   = "partner-files"
  role   = aws_iam_role.snowflake.id
  policy = data.aws_iam_policy_document.snowflake_access.json
}

# One notification for the whole bucket. Snowflake uses a single queue per bucket for all the
# auto-ingest pipes of the account, and each pipe keeps only the events under its own stage path.
# The queue is only known once the first pipe exists: that is the second apply of an environment.
resource "aws_s3_bucket_notification" "snowpipe" {
  count  = var.snowpipe_sqs_arn == null ? 0 : 1
  bucket = aws_s3_bucket.landing.id

  queue {
    id        = "snowpipe-auto-ingest"
    queue_arn = var.snowpipe_sqs_arn
    events    = ["s3:ObjectCreated:*"]
  }
}
