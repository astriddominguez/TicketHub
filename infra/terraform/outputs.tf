output "gateway_url" {
  description = "Where TicketHub is reachable."
  value       = "http://localhost:${var.gateway_port}"
}

output "images" {
  description = "Image tags deployed (a hash of each service's source code)."
  value       = local.images
}

output "kubectl_context" {
  description = "Use it with: kubectl --context <this> get pods"
  value       = "kind-${var.cluster_name}"
}
