output "docs_bucket_name" {
  value = module.rag_stack.docs_bucket_name
}

output "vector_bucket_name" {
  value = module.rag_stack.vector_bucket_name
}

output "vector_index_name" {
  value = module.rag_stack.vector_index_name
}

output "api_url" {
  value = module.rag_stack.api_url
}

output "ask_url" {
  value = module.rag_stack.ask_url
}

output "query_function_name" {
  value = module.rag_stack.query_function_name
}

output "query_log_group_name" {
  value = module.rag_stack.query_log_group_name
}

output "web_url" {
  value = module.rag_stack.web_url
}

output "web_bucket_name" {
  value = module.rag_stack.web_bucket_name
}

output "web_distribution_id" {
  value = module.rag_stack.web_distribution_id
}

output "ingest_queue_url" {
  value = module.rag_stack.ingest_queue_url
}

output "ingest_dlq_url" {
  value = module.rag_stack.ingest_dlq_url
}

output "ingest_function_name" {
  value = module.rag_stack.ingest_function_name
}

output "ingest_log_group_name" {
  value = module.rag_stack.ingest_log_group_name
}
