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

output "ingest_function_name" {
  value = module.rag_stack.ingest_function_name
}

output "ingest_log_group_name" {
  value = module.rag_stack.ingest_log_group_name
}
