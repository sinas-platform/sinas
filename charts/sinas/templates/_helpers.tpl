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
Image tag: explicit .Values.image.tag wins; empty falls back to the chart's
appVersion, which CI stamps from the release tag — so a versioned install
pulls that version's images instead of whatever `latest` points at (or, for
images only built on tags, an ImagePullBackOff).
*/}}
{{- define "sinas.imageTag" -}}
{{- .Values.image.tag | default .Chart.AppVersion -}}
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
Where the Redis server CA is mounted (redis.external.tls.caSecret /
caConfigMap). REDIS_SSL_CA_CERTS points here.
*/}}
{{- define "sinas.redisCaPath" -}}
/etc/sinas/redis-ca/ca.crt
{{- end }}

{{/*
"true" when the external Redis is reached over TLS, else empty. The one
place tls.enabled is read, so `true`, "true" (--set-string) and a stray
"false" string all mean the same everywhere. Paren-safe for
`helm upgrade --reuse-values` from a release without redis.external.
*/}}
{{- define "sinas.redisTlsOn" -}}
{{- $ext := (.Values.redis).external | default dict }}
{{- $tls := $ext.tls | default dict }}
{{- if and $ext.host (eq (toString ($tls.enabled | default false)) "true") }}true{{- end }}
{{- end }}

{{/*
Non-empty when a Redis server CA is mounted into the pods. Fails the render
on contradictory TLS settings.
*/}}
{{- define "sinas.redisCaEnabled" -}}
{{- $tls := ((.Values.redis).external | default dict).tls | default dict }}
{{- if include "sinas.redisTlsOn" . }}
{{- if and $tls.caSecret $tls.caConfigMap }}
{{- fail "redis.external.tls: set caSecret OR caConfigMap, not both" }}
{{- end }}
{{- if or $tls.caSecret $tls.caConfigMap }}true{{- end }}
{{- end }}
{{- end }}

{{/*
Redis connection environment (part of backendEnv). In-cluster: the
unauthenticated `redis` Service. External (redis.external.host): REDIS_URL
carries scheme (rediss:// = TLS), host, port and db; the AUTH string comes
from the operator's Secret as REDIS_PASSWORD, a separate variable the
backend uses verbatim, so it never needs URL-encoding and never sits in the
Deployment spec or in values.
tls.checkHostname: unset = auto in the backend (on with the system trust
store, off with a private CA); true/false is passed through explicitly.
*/}}
{{- define "sinas.redisEnv" -}}
{{- $ext := (.Values.redis).external | default dict }}
{{- if $ext.host }}
{{- $tls := $ext.tls | default dict }}
{{- $tlsOn := include "sinas.redisTlsOn" . }}
{{- $host := $ext.host }}
{{- if contains ":" $host }}{{- $host = printf "[%s]" $host }}{{- end }}
{{- with $ext.existingSecret }}
- name: REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ . | quote }}
      key: {{ $ext.existingSecretKey | default "password" | quote }}
{{- end }}
{{- with $ext.username }}
- name: REDIS_USERNAME
  value: {{ . | quote }}
{{- end }}
- name: REDIS_URL
  value: {{ printf "%s://%s:%d/%d" (ternary "rediss" "redis" (eq $tlsOn "true")) $host (($ext.port | default 6379) | int) (($ext.database | default 0) | int) | quote }}
{{- if $tlsOn }}
{{- if include "sinas.redisCaEnabled" . }}
- name: REDIS_SSL_CA_CERTS
  value: {{ include "sinas.redisCaPath" . | quote }}
{{- end }}
{{- $check := toString $tls.checkHostname }}
{{- if or (eq $check "true") (eq $check "false") }}
- name: REDIS_SSL_CHECK_HOSTNAME
  value: {{ $check | quote }}
{{- end }}
{{- end }}
{{- else }}
- name: REDIS_URL
  value: "redis://redis:6379/0"
{{- end }}
{{- end }}

{{/*
Redis server CA volume mount / volume — empty unless a CA is configured.
Every pod that gets backendEnv (and so talks to Redis) includes both.
*/}}
{{- define "sinas.redisCaMount" -}}
{{- if include "sinas.redisCaEnabled" . }}
- name: redis-ca
  mountPath: /etc/sinas/redis-ca
  readOnly: true
{{- end }}
{{- end }}

{{- define "sinas.redisCaVolume" -}}
{{- if include "sinas.redisCaEnabled" . }}
{{- $tls := .Values.redis.external.tls }}
- name: redis-ca
{{- if $tls.caSecret }}
  secret:
    secretName: {{ $tls.caSecret | quote }}
    items:
      - key: {{ $tls.caSecretKey | default "ca.crt" | quote }}
        path: ca.crt
{{- else }}
  configMap:
    name: {{ $tls.caConfigMap | quote }}
    items:
      - key: {{ $tls.caConfigMapKey | default "ca.crt" | quote }}
        path: ca.crt
{{- end }}
{{- end }}
{{- end }}

{{/*
"true" when the split profile runs a sub-agent worker (queue-agent-sub).
A missing queueAgentSub block or key means ON — `helm upgrade
--reuse-values` from a release that predates it keeps the worker (and the
issue #90 protection); only an explicit enabled: false or replicas: 0
turns it off.
*/}}
{{- define "sinas.queueAgentSubOn" -}}
{{- $qs := .Values.queueAgentSub | default dict }}
{{- $enabled := true }}
{{- if and (hasKey $qs "enabled") (not (kindIs "invalid" $qs.enabled)) }}
{{- $enabled = eq (toString $qs.enabled) "true" }}
{{- end }}
{{- $replicas := 1 }}
{{- if and (hasKey $qs "replicas") (not (kindIs "invalid" $qs.replicas)) }}
{{- $replicas = $qs.replicas | int }}
{{- end }}
{{- if and $enabled (gt $replicas 0) }}true{{- end }}
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
{{- include "sinas.redisEnv" . }}
## Executor selection — k8s-native: sandbox code runs in ephemeral pods
## created via the Kubernetes API (no Docker socket anywhere).
- name: SANDBOX_EXECUTOR
  value: {{ .Values.executor.sandbox | quote }}
- name: TRUSTED_EXECUTOR
  value: {{ .Values.executor.trusted | quote }}
{{- $executorImage := .Values.executor.image | default (printf "%s/executor:%s" .Values.image.registry (include "sinas.imageTag" .)) }}
- name: FUNCTION_CONTAINER_IMAGE
  value: {{ $executorImage | quote }}
- name: K8S_SANDBOX_IMAGE
  value: {{ $executorImage | quote }}
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
{{/*
Auto auth mode. OTP needs SMTP (codes arrive by email); without SMTP the
only way in is a password. With no superadminPassword either, the operator
mints a one-time setup link with `python -m app.cli setup-link` (see NOTES.txt).
*/}}
{{- $hasSmtp := ne (.Values.smtp.host | default "") "" }}
{{- $auto := "otp" }}
{{- if .Values.superadminPassword }}
{{- $auto = ternary "password+otp" "password" $hasSmtp }}
{{- else if not $hasSmtp }}
{{- $auto = "password" }}
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
{{- /* Split profile without a sub-agent worker (queueAgentSub.enabled
       explicitly false, or replicas 0): delegated agent jobs must go to the
       main agent queue, or nothing would consume them. Never in allInOne:
       the backend runs the sub-agent worker itself whenever routing is on. */}}
{{- if and (not .Values.allInOne) (not (include "sinas.queueAgentSubOn" .)) }}
- name: AGENT_SUBAGENT_QUEUE
  value: "false"
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
{{- include "sinas.otelEnv" . }}
- name: BACKEND_PORT
  value: "8000"
{{/* public_base_url (setup link, OAuth callback, public file URLs) derives
     from DOMAIN; without it the backend assumes http://localhost:8000. Skip
     when extraEnv sets it explicitly so the container spec has no duplicate. */}}
{{- $domainInExtra := false }}
{{- range .Values.extraEnv }}{{- if eq .name "DOMAIN" }}{{- $domainInExtra = true }}{{- end }}{{- end }}
{{- if and .Values.domain (not $domainInExtra) }}
- name: DOMAIN
  value: {{ .Values.domain | quote }}
{{- end }}
{{- with .Values.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/*
OpenTelemetry environment (part of backendEnv). Exporters go to
OTEL_EXPORTERS as JSON without their headersSecret; each headersSecret
becomes OTEL_EXPORTER_<NAME>_HEADERS from that Secret, so credentials never
sit in the Deployment spec. Paren-safe for `--reuse-values` from a release
without an `otel` key.
*/}}
{{- define "sinas.otelEnv" -}}
{{- $otel := .Values.otel | default dict }}
{{- if $otel.enabled }}
- name: OTEL_ENABLED
  value: "true"
{{- /* Entries with enabled: false are left out entirely, Secret reference
       included: a disabled exporter's Secret may be gone. */}}
{{- $active := list }}
{{- range ($otel.exporters | default list) }}
{{- if or (not (hasKey . "enabled")) .enabled }}
{{- $active = append $active . }}
{{- end }}
{{- end }}
{{- $exporters := list }}
{{- range $active }}
{{- $exporters = append $exporters (omit . "headersSecret") }}
{{- end }}
{{- if $exporters }}
- name: OTEL_EXPORTERS
  value: {{ toJson $exporters | quote }}
{{- end }}
{{- range $active }}
{{- $name := required "otel.exporters[].name is required" .name }}
{{- with .headersSecret }}
- name: OTEL_EXPORTER_{{ regexReplaceAll "[^A-Za-z0-9]" $name "_" | upper }}_HEADERS
  valueFrom:
    secretKeyRef:
      name: {{ required "otel.exporters[].headersSecret.name is required" .name | quote }}
      key: {{ .key | default "headers" | quote }}
{{- end }}
{{- end }}
{{- with $otel.serviceName }}
- name: OTEL_SERVICE_NAME
  value: {{ . | quote }}
{{- end }}
{{- with $otel.resourceAttributes }}
- name: OTEL_RESOURCE_ATTRIBUTES
  value: {{ . | quote }}
{{- end }}
{{- with $otel.sampler }}
- name: OTEL_TRACES_SAMPLER
  value: {{ . | quote }}
{{- end }}
{{- with $otel.samplerArg }}
- name: OTEL_TRACES_SAMPLER_ARG
  value: {{ . | quote }}
{{- end }}
{{- if ($otel.logs).enabled }}
- name: OTEL_LOGS_ENABLED
  value: "true"
- name: OTEL_LOGS_LEVEL
  value: {{ ($otel.logs).level | default "INFO" | quote }}
{{- end }}
- name: OTEL_HTTP_CLIENT_PROPAGATION
  value: {{ $otel.httpClientPropagation | default "none" | quote }}
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
