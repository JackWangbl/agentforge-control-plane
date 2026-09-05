package com.example.item.dto;

import java.util.List;
import java.util.Map;


public class ItemResponse {
    String service;
    Integer port;
    List<Long> ids;
    List<Map<String, Object>> items;

    public String getService() {
        return service;
    }

    public void setService(String service) {
        this.service = service;
    }

    public Integer getPort() {
        return port;
    }

    public void setPort(Integer port) {
        this.port = port;
    }

    public List<Long> getIds() {
        return ids;
    }

    public void setIds(List<Long> ids) {
        this.ids = ids;
    }

    public List<Map<String, Object>> getItems() {
        return items;
    }

    public void setItems(List<Map<String, Object>> items) {
        this.items = items;
    }

    public ItemResponse(String service, Integer port, List<Long> ids, List<Map<String, Object>> items) {
        this.service = service;
        this.port = port;
        this.ids = ids;
        this.items = items;
    }
}
