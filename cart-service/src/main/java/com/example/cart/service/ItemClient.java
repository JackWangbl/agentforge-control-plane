package com.example.cart.service;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;

import java.util.List;

@FeignClient("item-service")
public interface ItemClient {

    @RequestMapping("/items")
    public ItemResponse getItemsByIds(@RequestParam("ids") List<Long> ids);
}
