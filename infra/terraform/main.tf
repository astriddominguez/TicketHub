locals {
  repo_root = abspath("${path.module}/../..")

  # Image tags derived from the source code: change one line and the tag changes,
  # so the plan shows a new image and Helm rolls the pods. No change, no diff.
  image_tags = {
    for service in ["catalog", "booking"] : service => substr(sha1(join("", concat(
      [for f in sort(fileset("${local.repo_root}/services/${service}", "**/*.py")) :
      filesha1("${local.repo_root}/services/${service}/${f}") if !strcontains(f, "/tests/")],
      [
        filesha1("${local.repo_root}/services/${service}/Dockerfile"),
        filesha1("${local.repo_root}/services/${service}/pyproject.toml"),
        filesha1("${local.repo_root}/uv.lock"),
      ],
    ))), 0, 12)
  }
  images = { for service, tag in local.image_tags : service => "tickethub-${service}:${tag}" }

  # The .env as a map: KEY=value lines, comments and blank lines skipped,
  # surrounding quotes removed.
  env_lines = [
    for line in split("\n", file("${path.module}/${var.env_file}")) : trimspace(line)
    if can(regex("^[A-Za-z_][A-Za-z0-9_]*=", trimspace(line)))
  ]
  env = {
    for line in local.env_lines :
    split("=", line)[0] => trim(join("=", slice(split("=", line), 1, length(split("=", line)))), "\"")
  }
}

# ---------------------------------------------------------------- the cluster
resource "kind_cluster" "this" {
  name           = var.cluster_name
  wait_for_ready = true

  kind_config {
    kind        = "Cluster"
    api_version = "kind.x-k8s.io/v1alpha4"

    node {
      role = "control-plane"
      # The gateway's NodePort, published on your machine (127.0.0.1 only).
      extra_port_mappings {
        container_port = 30080
        host_port      = var.gateway_port
        listen_address = "127.0.0.1"
        protocol       = "TCP"
      }
    }
    node {
      role = "worker"
    }
    node {
      role = "worker"
    }
  }
}

# ---------------------------------------------------------------- the images
# Terraform has no "kind load" resource, so this one runs commands. It reruns
# whenever an image tag (i.e. the source code) or the cluster changes.
resource "terraform_data" "images" {
  triggers_replace = {
    cluster = kind_cluster.this.id
    images  = jsonencode(local.images)
  }

  provisioner "local-exec" {
    working_dir = local.repo_root
    command     = <<-EOT
      set -e
      %{for service, image in local.images~}
      docker build -q -f services/${service}/Dockerfile -t ${image} .
      kind load docker-image ${image} --name ${var.cluster_name}
      %{endfor~}
    EOT
    interpreter = ["bash", "-c"]
  }
}

# ---------------------------------------------------------------- the secret
# data_wo is "write-only": sent to the cluster, never stored in the Terraform
# state. Since Terraform can't compare a value it doesn't keep, the revision
# (derived from a hash of .env) tells it when to send a new one.
resource "kubernetes_secret_v1" "env" {
  metadata {
    name = "tickethub-env"
  }
  data_wo          = local.env
  data_wo_revision = parseint(substr(sha1(jsonencode(local.env)), 0, 7), 16)
}

# ---------------------------------------------------------------- the app
resource "helm_release" "tickethub" {
  name    = "tickethub"
  chart   = "${path.module}/../k8s/charts/tickethub"
  wait    = true
  timeout = 600

  values = [yamlencode({
    images = {
      catalog = local.images["catalog"]
      booking = local.images["booking"]
    }
    secretName = kubernetes_secret_v1.env.metadata[0].name
    publicUrl  = "http://localhost:${var.gateway_port}"
  })]

  depends_on = [terraform_data.images]
}
