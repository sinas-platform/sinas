{{/*
Expand the name of the chart.
*/}}
{{- define "sinas.name" -}}
{{- .Release.Name }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "sinas.labels" -}}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels for a given component
*/}}
{{- define "sinas.selectorLabels" -}}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: {{ . }}
{{- end }}

{{/*
ServiceAccount names
*/}}
{{- define "sinas.executorManagerSA" -}}
{{ .Release.Name }}-executor-manager
{{- end }}

{{- define "sinas.sandboxSA" -}}
{{ .Release.Name }}-sandbox
{{- end }}

{{/*
Image pull secret reference — shared by all sinas deployments.
*/}}
{{- define "sinas.imagePullSecrets" -}}
{{- if and .Values.registry .Values.registry.username }}
imagePullSecrets:
  - name: {{ .Release.Name }}-pull-secret
{{- end }}
{{- end }}

{{/*
Scheduling — nodeSelector / tolerations / affinity for one workload.

Call with: (dict "values" (.Values.backend) "root" . "inherit" true)

inherit=true  — stateless Deployments. Falls back to the global
                .Values.scheduling defaults when the workload sets nothing,
                so operators can move the whole stateless tier onto Spot /
                preemptible capacity with one setting.
inherit=false — StatefulSets (postgres / redis / clickhouse). Per-workload
                settings ONLY: the global default must never reach them
                implicitly. These sit on single-attach ReadWriteOnce
                volumes, where a preemption is a database restart with a
                volume reattach — not a rolling handoff. Placing them
                deliberately requires setting postgres.nodeSelector (etc.)
                by name.

tolerations matter beyond Autopilot: GKE Autopilot injects the Spot
toleration for you, GKE Standard and self-managed clusters do not.
*/}}
{{- define "sinas.scheduling" -}}
{{- $w := .values | default dict }}
{{- $g := ternary ((.root.Values).scheduling | default dict) dict .inherit }}
{{- with ($w.nodeSelector | default $g.nodeSelector) }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with ($w.tolerations | default $g.tolerations) }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with ($w.affinity | default $g.affinity) }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}

{{/*
Database password secretKeyRef body — release secret by default, or the
operator's pre-created secret when postgres.external.existingSecret is set.
Shared by backendEnv and pgbouncer.
*/}}
{{- define "sinas.dbPasswordRef" -}}
{{- if ((.Values.postgres).external).existingSecret -}}
name: {{ .Values.postgres.external.existingSecret }}
key: {{ .Values.postgres.external.existingSecretKey | default "password" }}
{{- else -}}
name: {{ .Release.Name }}-secrets
key: database-password
{{- end }}
{{- end }}

{{/*
Backend environment — shared by backend, all workers, scheduler, cdc-worker
*/}}
{{- define "sinas.backendEnv" -}}
- name: DATABASE_PASSWORD
  valueFrom:
    secretKeyRef:
      {{- include "sinas.dbPasswordRef" . | nindent 6 }}
- name: DATABASE_USER
  value: {{ .Values.postgres.user | quote }}
- name: DATABASE_HOST
  value: pgbouncer
- name: DATABASE_PORT
  value: "5432"
- name: DATABASE_NAME
  value: {{ .Values.postgres.database | quote }}
- name: DATABASE_URL
  value: "postgresql://{{ .Values.postgres.user }}:$(DATABASE_PASSWORD)@pgbouncer:5432/{{ .Values.postgres.database }}"
- name: DATABASE_DIRECT_HOST
  value: {{ ((.Values.postgres).external).host | default "postgres" | quote }}
{{- if .Values.clickhouse.enabled }}
- name: CLICKHOUSE_HOST
  value: clickhouse
- name: CLICKHOUSE_PORT
  value: "8123"
- name: CLICKHOUSE_USER
  value: {{ .Values.clickhouse.user | quote }}
- name: CLICKHOUSE_DATABASE
  value: {{ .Values.clickhouse.database | quote }}
{{- else }}
## Empty host = explicit disable: the backend skips ClickHouse entirely
## (no connection retries, logging methods no-op, log queries return empty).
- name: CLICKHOUSE_HOST
  value: ""
{{- end }}
- name: REDIS_URL
  value: "redis://redis:6379/0"
- name: BUILDER_URL
  value: "http://builder:3000"
## Executor selection — k8s-native: sandbox code runs in ephemeral pods
## created via the Kubernetes API (no Docker socket anywhere).
- name: SANDBOX_EXECUTOR
  value: {{ .Values.executor.sandbox | quote }}
- name: TRUSTED_EXECUTOR
  value: {{ .Values.executor.trusted | quote }}
- name: FUNCTION_CONTAINER_IMAGE
  value: {{ .Values.executor.image | quote }}
- name: K8S_SANDBOX_IMAGE
  value: {{ .Values.executor.image | quote }}
- name: K8S_SANDBOX_SERVICE_ACCOUNT
  value: {{ include "sinas.sandboxSA" . | quote }}
- name: K8S_SANDBOX_INSTALL_DEPENDENCIES
  value: {{ .Values.executor.installDependencies | quote }}
- name: K8S_RELEASE_NAME
  value: {{ .Release.Name | quote }}
- name: K8S_TRUSTED_WORKERS
  value: {{ .Values.executor.trustedWorkers | default 2 | quote }}
- name: K8S_SANDBOX_POD_READY_TIMEOUT
  value: {{ .Values.executor.podReadyTimeout | quote }}
- name: POD_NAMESPACE
  valueFrom:
    fieldRef:
      fieldPath: metadata.namespace
- name: SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Release.Name }}-secrets
      key: secret-key
- name: ENCRYPTION_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Release.Name }}-secrets
      key: encryption-key
{{- if .Values.clickhouse.enabled }}
- name: CLICKHOUSE_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Release.Name }}-secrets
      key: clickhouse-password
{{- end }}
{{- $auto := "otp" }}
{{- if .Values.superadminPassword }}
{{- $auto = ternary "password+otp" "password" (ne .Values.smtp.host "") }}
{{- end }}
## Paren-safe: `helm upgrade --reuse-values` from a pre-auth release has no
## `auth` key at all, and a bare .Values.auth.mode nil-pointers there.
- name: AUTH_MODE
  value: {{ (.Values.auth).mode | default $auto | quote }}
{{- if .Values.superadminPassword }}
- name: SUPERADMIN_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Release.Name }}-secrets
      key: superadmin-password
{{- end }}
- name: CODE_EXECUTION_ENABLED
  value: {{ .Values.features.codeExecution | quote }}
- name: BUILTIN_DATABASE_ENABLED
  value: {{ .Values.features.builtinDatabase | quote }}
{{- with (.Values.jwt).algorithm }}
- name: JWT_ALGORITHM
  value: {{ . | quote }}
{{- end }}
{{- with (.Values.jwt).issuer }}
- name: JWT_ISSUER
  value: {{ . | quote }}
{{- end }}
{{- with (.Values.jwt).audience }}
- name: JWT_AUDIENCE
  value: {{ . | quote }}
{{- end }}
{{- if (.Values.jwt).existingSecret }}
- name: JWT_PRIVATE_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.jwt.existingSecret }}
      key: {{ .Values.jwt.existingSecretKey | default "jwt-private-key" }}
{{- end }}
- name: BACKEND_PORT
  value: "8000"
{{- with .Values.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/*
File storage volume mount — /var/sinas/files
*/}}
{{- define "sinas.fileStorageMount" -}}
- name: file-storage
  mountPath: /var/sinas/files
{{- end }}

{{/*
File storage volume reference.
Default: emptyDir (dev-friendly, no cross-pod sharing, non-persistent).
Set fileStorage.storageClass to enable a PVC with an RWX storage class for
production (Longhorn, NFS, etc.) — the only way to share files across pods
on multiple nodes.
*/}}
{{- define "sinas.fileStorageVolume" -}}
{{- if (.Values.fileStorage).storageClass }}
- name: file-storage
  persistentVolumeClaim:
    claimName: {{ .Release.Name }}-file-storage
{{- else }}
- name: file-storage
  emptyDir: {}
{{- end }}
{{- end }}
