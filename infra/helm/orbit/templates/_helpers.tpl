{{- define "orbit.env" -}}
- name: KAFKA_BOOTSTRAP
  value: {{ .Values.kafka.bootstrap | quote }}
- name: DATABASE_URL
  valueFrom: {secretKeyRef: {name: orbit-db, key: url}}
- name: DATABASE_APP_URL
  valueFrom: {secretKeyRef: {name: orbit-db, key: appUrl}}
- name: REDIS_URL
  value: {{ .Values.redis.url | quote }}
- name: S3_ENDPOINT
  value: {{ .Values.s3.endpoint | quote }}
- name: S3_BUCKET
  value: {{ .Values.s3.bucket | quote }}
- name: S3_ACCESS_KEY
  valueFrom: {secretKeyRef: {name: {{ .Values.s3.secretName }}, key: accessKey}}
- name: S3_SECRET_KEY
  valueFrom: {secretKeyRef: {name: {{ .Values.s3.secretName }}, key: secretKey}}
- name: JWKS_URL
  value: {{ .Values.auth.jwksUrl | quote }}
- name: JWT_ISSUER
  value: {{ .Values.auth.issuer | quote }}
- name: JWT_DEV_SECRET
  valueFrom: {secretKeyRef: {name: {{ .Values.auth.devSecretName }}, key: secret}}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: "http://otel-collector:4317"
{{- end -}}
