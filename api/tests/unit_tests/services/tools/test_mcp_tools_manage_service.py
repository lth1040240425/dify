from unittest.mock import Mock

from services.tools.mcp_tools_manage_service import MCPToolManageService


def test_filter_dynamic_request_headers_for_tool_discovery() -> None:
    headers = {
        "X-Biz-Token": "Bearer {{request.headers.X-Biz-Token}}",
        "X-Trace-Id": "{{request.header.X-Trace-Id}}",
        "Authorization": "Bearer service-token",
        "X-Static-Header": "static-value",
    }

    assert MCPToolManageService._filter_dynamic_request_headers(headers) == {
        "Authorization": "Bearer service-token",
        "X-Static-Header": "static-value",
    }


def test_list_provider_tools_uses_filtered_headers_for_discovery() -> None:
    session = Mock()
    service = MCPToolManageService(session=session)
    provider_entity = Mock()
    provider_entity.authed = True
    provider_entity.decrypt_headers.return_value = {
        "X-Biz-Token": "Bearer {{request.headers.X-Biz-Token}}",
        "X-Trace-Id": "{{request.headers.X-Trace-Id}}",
        "X-Static-Header": "static-value",
    }
    provider_entity.retrieve_tokens.return_value = Mock(token_type="bearer", access_token="oauth-token")
    provider_entity.decrypt_server_url.return_value = "https://mcp.example.com"

    db_provider = Mock()
    db_provider.to_entity.return_value = provider_entity
    service.get_provider = Mock(return_value=db_provider)
    service._retrieve_remote_mcp_tools = Mock(return_value=[])
    service._build_tool_provider_response = Mock(return_value=Mock())

    service.list_provider_tools(tenant_id="tenant-id", provider_id="provider-id")

    service._retrieve_remote_mcp_tools.assert_called_once_with(
        "https://mcp.example.com",
        {
            "X-Static-Header": "static-value",
            "Authorization": "Bearer oauth-token",
        },
        provider_entity,
    )
