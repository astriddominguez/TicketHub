variable "cluster_name" {
  description = "Name of the local kind cluster."
  type        = string
  default     = "tickethub"
}

variable "gateway_port" {
  description = "Port on your machine where the gateway is reachable (http://localhost:<port>)."
  type        = number
  default     = 8090
}

variable "env_file" {
  description = "The .env with the secrets the services need. Its values end up in the Terraform state: never commit the state."
  type        = string
  default     = "../../.env"
}
