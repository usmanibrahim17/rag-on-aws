# IAM part of the deploy role: it may manage the stack's roles, but ONLY with the boundary.

data "aws_iam_policy_document" "deploy_iam" {
  statement {
    sid = "ReadStackRoles"
    actions = [
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
    ]
    resources = [local.role_arn]
  }

  # Creating a role, or giving a role permissions, is only allowed when the role carries the
  # boundary (for CreateRole: the boundary supplied in the request; for the others: the boundary
  # already attached to the target role).
  statement {
    sid       = "ChangeStackRolesOnlyWithBoundary"
    actions   = ["iam:CreateRole", "iam:PutRolePolicy", "iam:AttachRolePolicy"]
    resources = [local.role_arn]

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [local.boundary_arn]
    }
  }

  # Housekeeping that cannot add permissions.
  statement {
    sid = "MaintainStackRoles"
    actions = [
      "iam:DeleteRole",
      "iam:UpdateRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
    ]
    resources = [local.role_arn]
  }

  statement {
    sid       = "PassStackRolesToLambda"
    actions   = ["iam:PassRole"]
    resources = [local.role_arn]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com"]
    }
  }

  # Nobody (via this role) may remove or swap the boundary of any role.
  statement {
    sid       = "DenyChangingBoundaries"
    effect    = "Deny"
    actions   = ["iam:PutRolePermissionsBoundary", "iam:DeleteRolePermissionsBoundary"]
    resources = ["*"]
  }

  # Nor edit the boundary policy itself, or the deploy role's own policies.
  statement {
    sid    = "DenyEditingBoundaryAndDeployPolicies"
    effect = "Deny"
    actions = [
      "iam:CreatePolicy",
      "iam:CreatePolicyVersion",
      "iam:DeletePolicy",
      "iam:DeletePolicyVersion",
      "iam:SetDefaultPolicyVersion",
    ]
    resources = [
      local.boundary_arn,
      "arn:aws:iam::${local.acct}:policy/${local.deploy_role_name}-*",
    ]
  }

  # The deploy role must never be able to change itself (no self-escalation).
  statement {
    sid    = "DenyChangingTheDeployRole"
    effect = "Deny"
    actions = [
      "iam:DeleteRole",
      "iam:UpdateRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
    ]
    resources = [local.deploy_role]
  }
}
