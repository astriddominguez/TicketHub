{{/* Labels every resource gets. */}}
{{- define "tickethub.labels" -}}
app.kubernetes.io/part-of: tickethub
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{/*
Environment of every application container: secrets from .env, then the
in-cluster addresses (they override .env's localhost ones).
*/}}
{{- define "tickethub.appEnv" -}}
envFrom:
  - secretRef:
      name: {{ .Values.secretName }}
  - configMapRef:
      name: tickethub-config
env:
  # $(VAR) is expanded by Kubernetes from the variables above (from the secret).
  - name: RABBITMQ_URL
    value: "amqp://$(RABBITMQ_USER):$(RABBITMQ_PASSWORD)@rabbitmq:5672/"
{{- end }}

{{/* Container hardening shared by every application container. */}}
{{- define "tickethub.securityContext" -}}
securityContext:
  runAsNonRoot: true
  allowPrivilegeEscalation: false
  capabilities:
    drop: ["ALL"]
{{- end }}
